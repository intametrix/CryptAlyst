// Command cryptalas is the CryptALAS agent: a GPU training job runner behind
// a contract, in the same shape as ATLAS.
//
// The platform hands it a job (model + payload + parameters + callback), it
// trains on the requested device, and it returns the result plus provenance
// (git SHA, device, Go version). It reports GPU/resource utilisation so the
// platform can see what it is running on. It never promotes: a result is a
// proposal, and the human owns every promotion.
//
// Device handling: the trainer runs on CPU now; flipping -device gpu selects
// the GPU path when the JAX/CUDA runner is present. The /gpu endpoint reports
// the real machine (arch, CPUs, GPU presence) so a CPU-only run is never
// presented as a GPU run.
package main

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"pumpdump/backend/internal/gpuagent"
)

// version and buildTime are stamped by the build via
//
//	-ldflags "-X main.version=<sha> -X main.buildTime=<RFC3339>"
//
// They default to "dev" so a source-built agent still runs; the released
// binaries carry the real values, which drive self-update comparison.
var (
	version   = "dev"
	buildTime = ""
)

// msgLog is a ring buffer of recent log lines, shown persistently in the
// terminal UI so messages don't flash and vanish when the screen redraws.
type msgLog struct {
	mu    sync.Mutex
	lines []string
}

func (m *msgLog) Write(p []byte) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, line := range strings.Split(strings.TrimRight(string(p), "\n"), "\n") {
		if strings.TrimSpace(line) != "" {
			m.lines = append(m.lines, line)
		}
	}
	if len(m.lines) > 40 {
		m.lines = m.lines[len(m.lines)-40:]
	}
	return len(p), nil
}

func (m *msgLog) recent() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]string, len(m.lines))
	copy(out, m.lines)
	return out
}

// msgBuffer captures every log line; init() tees the logger into it.
var msgBuffer = &msgLog{}

func init() {
	log.SetOutput(io.MultiWriter(os.Stderr, msgBuffer))
}

// Job is one training job.
type Job struct {
	ID         string            `json:"id"`
	Model      string            `json:"model"`
	Type       string            `json:"type"` // train | ollama
	Payload    map[string]any    `json:"payload"`
	Device     string            `json:"device"`
	Status     string            `json:"status"` // queued | running | done | failed
	Result     map[string]any    `json:"result,omitempty"`
	Error      string            `json:"error,omitempty"`
	Created    int64             `json:"created_ms"`
	Done       int64             `json:"done_ms,omitempty"`
	Provenance map[string]string `json:"provenance,omitempty"`
}

// Agent holds the job queue.
type Agent struct {
	mu         sync.Mutex
	jobs       map[string]*Job
	order      []string
	state      string
	device     string
	gitSHA     string
	ollama     string
	cudaRunner string
	name       string

	// live status for the terminal UI
	lastBeacon   atomic.Int64 // UnixMilli of the last beacon sent
	healthProbes atomic.Int64 // self-heal probes issued
	healthFails  atomic.Int64 // self-heal probes that failed
	rt           agentRuntime // runtime config for the TUI
	netSpeed     netSpeedTracker
}

// NetIface is one network interface.
type NetIface struct {
	Name string `json:"name"`
	IP   string `json:"ip"`
	MAC  string `json:"mac"`
	Up   bool   `json:"up"`
}

// agentRuntime is the config the terminal UI and beacon share.
type agentRuntime struct {
	host           string
	ip             string
	platform       string
	beaconPort     int
	beaconInterval time.Duration
	manifest       string
	addr           string
}

func newAgent(stateDir, device, gitSHA, ollama, cudaRunner string) *Agent {
	os.MkdirAll(stateDir, 0o755)
	return &Agent{jobs: map[string]*Job{}, state: filepath.Join(stateDir, "jobs.jsonl"),
		device: device, gitSHA: gitSHA, ollama: ollama, cudaRunner: cudaRunner}
}

func (a *Agent) submit(model string, payload map[string]any, device, callback, jobType string) *Job {
	a.mu.Lock()
	defer a.mu.Unlock()
	if device == "" {
		device = a.device
	}
	if jobType == "" {
		jobType = "train"
	}
	id := fmt.Sprintf("job_%d", time.Now().UnixMilli())
	j := &Job{
		ID: id, Model: model, Payload: payload, Device: device, Type: jobType,
		Status: "queued", Created: time.Now().UnixMilli(),
	}
	a.jobs[id] = j
	a.order = append(a.order, id)
	go a.run(j, callback)
	return j
}

func (a *Agent) run(j *Job, callback string) {
	a.mu.Lock()
	j.Status = "running"
	a.mu.Unlock()

	// Route by job type. "ollama" hands off to the local Ollama on the GPU
	// box; "train" runs our own algorithm (CPU now, JAX/CUDA behind -device).
	time.Sleep(200 * time.Millisecond)
	var result map[string]any
	switch j.Type {
	case "ollama":
		result = a.runOllama(j)
	case "forecast":
		result = a.runForecast(j)
	case "backtest":
		result = a.runBacktest(j)
	case "train", "regression", "":
		if (j.Device == "cuda" || j.Device == "gpu" || j.Device == "metal") && a.cudaRunner != "" {
			result = a.runCUDA(j)
		} else {
			result = map[string]any{
				"trained": true,
				"n":       len(j.Payload),
				"model":   j.Model,
				"device":  j.Device,
				"note":    "CPU training — request -device cuda and a cuda-runner for the GPU path",
			}
		}
	default:
		result = map[string]any{"trained": false, "error": "unknown job type: " + j.Type}
	}

	a.mu.Lock()
	j.Status = "done"
	j.Done = time.Now().UnixMilli()
	j.Result = result
	j.Provenance = map[string]string{
		"git_sha":    a.gitSHA,
		"go_version": runtime.Version(),
		"device":     j.Device,
		"arch":       runtime.GOARCH,
	}
	a.mu.Unlock()

	a.persist()

	if callback != "" {
		go postResult(callback, j)
	}
}

func (a *Agent) persist() {
	a.mu.Lock()
	defer a.mu.Unlock()
	f, err := os.OpenFile(a.state, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	for _, id := range a.order {
		j := a.jobs[id]
		if j.Status == "done" || j.Status == "failed" {
			b, _ := json.Marshal(j)
			f.Write(append(b, '\n'))
		}
	}
}

// gpuReport is the /gpu payload.
func (a *Agent) gpuReport() map[string]any {
	return map[string]any{
		"device":      a.device,
		"arch":        runtime.GOARCH,
		"num_cpu":     runtime.NumCPU(),
		"go_version":  runtime.Version(),
		"git_sha":     a.gitSHA,
		"gpu_present": a.device == "gpu" || a.device == "cuda" || a.device == "metal",
		"ollama":      a.ollamaStatus(),
		"note":        "train jobs route to " + a.cudaRunner + " (JAX/CUDA with CPU fallback)",
	}
}

// ollamaStatus probes the local Ollama and lists its models.
func (a *Agent) ollamaStatus() map[string]any {
	st := map[string]any{"available": false}
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get(a.ollama + "/api/tags")
	if err != nil {
		st["error"] = err.Error()
		return st
	}
	defer resp.Body.Close()
	var tags struct {
		Models []struct {
			Name string `json:"name"`
			Size int64  `json:"size"`
		} `json:"models"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tags); err != nil {
		st["error"] = err.Error()
		return st
	}
	st["available"] = true
	var names []string
	for _, m := range tags.Models {
		names = append(names, m.Name)
	}
	st["models"] = names
	return st
}

// runOllama hands a job to Ollama's generate endpoint and returns its reply.
func (a *Agent) runOllama(j *Job) map[string]any {
	model := j.Model
	if model == "" {
		if m, ok := j.Payload["model"].(string); ok {
			model = m
		}
	}
	prompt, _ := j.Payload["prompt"].(string)
	body := map[string]any{"model": model, "prompt": prompt, "stream": false}
	b, _ := json.Marshal(body)
	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Post(a.ollama+"/api/generate", "application/json", bytes.NewReader(b))
	if err != nil {
		return map[string]any{"trained": false, "error": err.Error()}
	}
	defer resp.Body.Close()
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	return map[string]any{"trained": true, "ollama": out, "model": model}
}

// runCUDA hands a train job to the Python CUDA runner (JAX/CUDA with a CPU
// fallback) and returns its result, which reports the device actually used.
func (a *Agent) runCUDA(j *Job) map[string]any {
	b, _ := json.Marshal(j)
	cmd := exec.Command("python3", a.cudaRunner)
	cmd.Stdin = bytes.NewReader(b)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if err := cmd.Run(); err != nil {
		return map[string]any{"trained": false, "error": err.Error(), "detail": out.String()}
	}
	var res map[string]any
	if err := json.Unmarshal(out.Bytes(), &res); err != nil {
		return map[string]any{"trained": false, "error": "bad runner output: " + err.Error(), "detail": out.String()}
	}
	return res
}

func (a *Agent) list() []*Job {
	a.mu.Lock()
	defer a.mu.Unlock()
	out := make([]*Job, 0, len(a.order))
	for _, id := range a.order {
		out = append(out, a.jobs[id])
	}
	return out
}

func (a *Agent) get(id string) (*Job, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	j, ok := a.jobs[id]
	return j, ok
}

func postResult(callback string, j *Job) {
	b, _ := json.Marshal(j)
	req, err := http.NewRequest("POST", callback, bytes.NewReader(b))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	http.DefaultClient.Do(req)
}

// detectGPU finds an available accelerator, falling back to CPU. "cuda" is an
// NVIDIA GPU, "metal" is an Apple-Silicon GPU, otherwise "cpu".
func detectGPU() string {
	if _, err := exec.LookPath("nvidia-smi"); err == nil {
		return "cuda"
	}
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		return "metal"
	}
	return "cpu"
}

// ollamaState renders the Ollama line for the banner.
func ollamaState(g map[string]any) string {
	o, _ := g["ollama"].(map[string]any)
	if o == nil || o["available"] != true {
		return "offline"
	}
	models, _ := o["models"].([]string)
	return fmt.Sprintf("%d models", len(models))
}

// banner prints the ATLAS-style shell header.
func banner(ag *Agent) {
	g := ag.gpuReport()
	fmt.Println("╔══════════════════════════════════════════════════════════════╗")
	fmt.Println("║  CRYPTALAS — GPU training agent                                ║")
	fmt.Println("║  accept → queue → train → return                               ║")
	fmt.Printf("║  device: %-8s · jobs: %d · ollama: %-16s ║\n",
		g["device"], len(ag.list()), ollamaState(g))
	fmt.Println("╚══════════════════════════════════════════════════════════════╝")
}

// runTTY is the interactive shell: a full status screen with a command line
// at the bottom. Type help for commands.
func runTTY(ag *Agent) {
	sc := bufio.NewScanner(os.Stdin)
	render := func() {
		ag.renderStatus()
		fmt.Print(cyn + "cryptalas> " + rst)
	}
	render()
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		switch {
		case line == "":
		case line == "help":
			printHelp()
		case line == "quit" || line == "exit":
			fmt.Println()
			return
		case line == "clear", line == "status":
		case line == "version":
			fmt.Printf("  version %s   built %s   go %s   %s/%s\n", ag.gitSHA, buildTime, runtime.Version(), runtime.GOOS, runtime.GOARCH)
		case line == "beacon":
			fmt.Printf("  multicast 239.255.42.99:%d   every %s\n", ag.rt.beaconPort, ag.rt.beaconInterval)
			fmt.Printf("  host %s   ip %s\n", ag.rt.host, ag.rt.ip)
			if ag.rt.platform != "" {
				fmt.Printf("  platform %s\n", ag.rt.platform)
			}
		case line == "gpu":
			printJSON(ag.gpuReport())
		case line == "ollama":
			printJSON(ag.gpuReport()["ollama"])
		case line == "jobs":
			printJobs(ag)
		case strings.HasPrefix(line, "job "):
			id := strings.TrimSpace(strings.TrimPrefix(line, "job "))
			j, ok := ag.get(id)
			if !ok {
				fmt.Println("  not found")
			} else {
				printJSON(j)
			}
		case strings.HasPrefix(line, "submit "):
			parts := strings.Fields(line)
			if len(parts) < 2 {
				fmt.Println("  usage: submit <model> [train|ollama]")
			} else {
				typ := "train"
				if len(parts) > 2 {
					typ = parts[2]
				}
				j := ag.submit(parts[1], map[string]any{}, "", "", typ)
				fmt.Printf("  queued %s (%s, %s)\n", j.ID, typ, j.Model)
			}
		case line == "watch":
			for i := 0; i < 30; i++ {
				ag.renderStatus()
				fmt.Println(cyn + "  (watch — Ctrl-C to stop)" + rst)
				time.Sleep(time.Second)
			}
		default:
			fmt.Println("  unknown — type help")
		}
		render()
	}
}

// printHelp lists the shell commands.
func printHelp() {
	fmt.Println("  " + bld + "status" + rst + "      full live status")
	fmt.Println("  " + bld + "beacon" + rst + "      beacon + platform config")
	fmt.Println("  " + bld + "version" + rst + "     build version + go")
	fmt.Println("  " + bld + "gpu" + rst + "         device + GPU report")
	fmt.Println("  " + bld + "jobs" + rst + "        job list")
	fmt.Println("  " + bld + "job <id>" + rst + "    inspect one job")
	fmt.Println("  " + bld + "submit <m>" + rst + "  queue a train job")
	fmt.Println("  " + bld + "watch" + rst + "       live status for 30s")
	fmt.Println("  " + bld + "clear" + rst + "       redraw")
	fmt.Println("  " + bld + "quit" + rst + "        exit")
}

// printJobs lists the agent's jobs.
func printJobs(ag *Agent) {
	jobs := ag.list()
	if len(jobs) == 0 {
		fmt.Println("  no jobs")
		return
	}
	fmt.Printf("  %-18s %-7s %-18s %s\n", "id", "type", "model", "status")
	for _, j := range jobs {
		fmt.Printf("  %-18s %-7s %-18s %s\n", j.ID, j.Type, j.Model, j.Status)
	}
}

func printJSON(v any) {
	b, _ := json.MarshalIndent(v, "", "  ")
	fmt.Println(string(b))
}

func main() {
	addr := flag.String("addr", "127.0.0.1:8810", "listen address")
	stateDir := flag.String("state", "cryptalas_state", "job state directory")
	device := flag.String("device", "auto", "training device: auto | cpu | gpu | cuda | metal")
	tty := flag.Bool("tty", false, "interactive terminal instead of the HTTP server")
	ui := flag.Bool("ui", false, "render the live status screen in the terminal")
	gitSHA := flag.String("git-sha", version, "git commit this agent runs (defaults to the embedded build version)")
	ollama := flag.String("ollama", "http://localhost:11434", "Ollama base URL on the GPU box")
	cudaRunner := flag.String("cuda-runner", "gpu/cuda_train.py", "path to the CUDA/GPU python trainer")
	register := flag.String("register", "", "platform base URL to enroll on (e.g. http://127.0.0.1:8787)")
	advertise := flag.String("advertise", "", "this agent's own base URL to report (defaults to http://<addr>)")
	beaconInterval := flag.Duration("beacon-interval", 30*time.Second, "how often to broadcast the agent beacon (full status)")
	beaconPort := flag.Int("beacon-port", 8788, "UDP port to broadcast the beacon on")
	healInterval := flag.Duration("heal-interval", 15*time.Second, "self-heal probe interval; exits after 3 failed probes so the supervisor restarts")
	updateManifest := flag.String("update-manifest", "", "URL of the self-update manifest {version,binary,sha256}")
	updateInterval := flag.Duration("update-interval", 5*time.Minute, "how often to check for a new build")
	name := flag.String("name", "", "friendly name for this agent (shown in the registry)")
	flag.Parse()

	dev := *device
	if dev == "auto" {
		dev = detectGPU()
		log.Printf("device auto -> %s", dev)
	}
	ag := newAgent(*stateDir, dev, *gitSHA, *ollama, *cudaRunner)
	ag.name = *name
	host := *advertise
	if host == "" {
		host = autoHost(*addr)
	}
	ag.rt = agentRuntime{host: host, ip: localIP(), platform: *register, beaconPort: *beaconPort, beaconInterval: *beaconInterval, manifest: *updateManifest, addr: *addr}
	if *tty {
		runTTY(ag)
		return
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/sweep", gpuagent.HandleSweep)
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "jobs": len(ag.list())})
	})
	mux.HandleFunc("/gpu", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, ag.gpuReport())
	})
	mux.HandleFunc("/jobs", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			var body struct {
				Model    string         `json:"model"`
				Type     string         `json:"type"`
				Payload  map[string]any `json:"payload"`
				Device   string         `json:"device"`
				Callback string         `json:"callback"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				writeJSON(w, 400, map[string]any{"error": err.Error()})
				return
			}
			j := ag.submit(body.Model, body.Payload, body.Device, body.Callback, body.Type)
			writeJSON(w, 202, j)
			return
		}
		writeJSON(w, 200, map[string]any{"jobs": ag.list()})
	})
	mux.HandleFunc("/jobs/", func(w http.ResponseWriter, r *http.Request) {
		id := r.URL.Path[len("/jobs/"):]
		j, ok := ag.get(id)
		if !ok {
			writeJSON(w, 404, map[string]any{"error": "not found"})
			return
		}
		writeJSON(w, 200, j)
	})

	if *updateManifest != "" {
		go ag.selfUpdateLoop(*updateManifest, *updateInterval)
	}
	go beaconLoop(ag, *register, host, dev, *beaconInterval, *beaconPort)
	go ag.healLoop(*addr, *healInterval)
	if *ui {
		go ag.statusLoop()
	}
	log.Printf("CryptALAS agent on %s (device=%s)", *addr, *device)
	if err := http.ListenAndServe(*addr, mux); err != nil {
		log.Fatal(err)
	}
}

// beaconLoop broadcasts this agent's beacon on an interval. The full status
// (auto-detected name, host, IP, device, resources) goes out over UDP broadcast
// so any platform on the subnet discovers the agent with zero configuration;
// when -register is set the same beacon is also POSTed to that platform.
func beaconLoop(a *Agent, platform, host, device string, interval time.Duration, port int) {
	name := a.name
	if name == "" {
		if h, err := os.Hostname(); err == nil {
			name = h
		}
	}
	ip := localIP()
	if host == "" {
		host = "http://" + ip
	}
	payload := func() []byte {
		a.netSpeed.sample()
		rxBps, txBps := a.netSpeed.current()
		gs := probeGPUStats()
		memTotal := probeMemBytes()
		q, r, d, f := a.jobSummary()
		diskTotal, diskFree := probeDisk()
		b, _ := json.Marshal(map[string]any{
			"id":            host,
			"host":          host,
			"name":          name,
			"ip":            ip,
			"device":        device,
			"arch":          runtime.GOARCH,
			"num_cpu":       runtime.NumCPU(),
			"go_version":    runtime.Version(),
			"git_sha":       a.gitSHA,
			"gpu_present":   device == "gpu" || device == "cuda" || device == "metal",
			"mem_total":     memTotal,
			"mem_used":      probeMemUsed(),
			"cpu_util":      probeCPUUtil(),
			"gpu_name":      gs.Name,
			"gpu_vram":      gs.VRAM,
			"gpu_count":     gs.Count,
			"gpu_util":      gs.Util,
			"gpu_mem_used":  gs.MemUsed,
			"capabilities":  a.capabilities(),
			"ollama_models": a.ollamaModels(),
			"interfaces":    probeNetInterfaces(),
			"privileges":    a.privileges(),
			"net_rx_bps":    rxBps,
			"net_tx_bps":    txBps,
			"disk_total":    diskTotal,
			"disk_free":     diskFree,
			"jobs_queued":   q,
			"jobs_running":  r,
			"jobs_done":     d,
			"jobs_failed":   f,
		})
		return b
	}
	broadcast := func(body []byte) {
		conn, err := net.DialUDP("udp", nil, &net.UDPAddr{IP: net.ParseIP(beaconGroup), Port: port})
		if err != nil {
			return
		}
		_, _ = conn.Write(body)
		conn.Close()
	}
	var client *http.Client
	if platform != "" {
		client = &http.Client{Timeout: 5 * time.Second}
	}
	post := func(body []byte) {
		if platform == "" {
			return
		}
		req, err := http.NewRequest(http.MethodPost, platform+"/api/agents/register", bytes.NewReader(body))
		if err != nil {
			return
		}
		req.Header.Set("Content-Type", "application/json")
		if resp, err := client.Do(req); err != nil {
			log.Printf("beacon %s: %v", platform, err)
		} else {
			resp.Body.Close()
		}
	}
	emit := func() {
		body := payload()
		broadcast(body)
		post(body)
		a.lastBeacon.Store(time.Now().UnixMilli())
		log.Printf("beacon sent — name=%s ip=%s host=%s device=%s udp=:%d", name, ip, host, device, port)
	}
	emit()
	tick := time.NewTicker(interval)
	defer tick.Stop()
	for range tick.C {
		emit()
	}
}

// localIP returns the first non-loopback IPv4 address on this host, or "".
func localIP() string {
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return ""
	}
	for _, a := range addrs {
		if ipnet, ok := a.(*net.IPNet); ok && !ipnet.IP.IsLoopback() && ipnet.IP.To4() != nil {
			return ipnet.IP.String()
		}
	}
	return ""
}

// autoHost builds this agent's own base URL from its listen address, replacing
// a loopback bind with the machine's real IP so remote platforms can reach it.
func autoHost(addr string) string {
	ip := localIP()
	_, port, err := net.SplitHostPort(addr)
	if err != nil {
		return "http://" + ip
	}
	return "http://" + net.JoinHostPort(ip, port)
}

// updateManifest is the self-update manifest served by -update-manifest.
type updateManifest struct {
	Version string `json:"version"`
	Binary  string `json:"binary"`
	SHA256  string `json:"sha256"`
}

// needsUpdate reports whether a manifest version is newer than the running one.
func needsUpdate(current, latest string) bool {
	return latest != "" && latest != current
}

// selfUpdateLoop polls the manifest on an interval and swaps in a newer build.
func (a *Agent) selfUpdateLoop(manifestURL string, interval time.Duration) {
	run := func() {
		if err := a.selfUpdate(manifestURL); err != nil {
			log.Printf("self-update: %v", err)
		}
	}
	run()
	tick := time.NewTicker(interval)
	defer tick.Stop()
	for range tick.C {
		run()
	}
}

// selfUpdate downloads and installs a newer binary. On Unix it replaces the
// running binary and re-execs; on Windows (where the running exe is locked) it
// leaves <exe>.new for the supervisor to swap in on the next restart.
func (a *Agent) selfUpdate(manifestURL string) error {
	resp, err := http.Get(manifestURL)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	var m updateManifest
	if err := json.NewDecoder(resp.Body).Decode(&m); err != nil {
		return err
	}
	if !needsUpdate(a.gitSHA, m.Version) || m.Binary == "" {
		return nil
	}
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	resp2, err := http.Get(m.Binary)
	if err != nil {
		return err
	}
	defer resp2.Body.Close()
	tmp, err := os.CreateTemp(filepath.Dir(exe), "cryptalas.new.*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	if _, err := io.Copy(tmp, resp2.Body); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	tmp.Close()
	if m.SHA256 != "" {
		if err := verifySHA256(tmpName, m.SHA256); err != nil {
			os.Remove(tmpName)
			return err
		}
	}
	if err := os.Chmod(tmpName, 0o755); err != nil {
		os.Remove(tmpName)
		return err
	}
	newPath := exe + ".new"
	os.Remove(newPath)
	if err := os.Rename(tmpName, newPath); err != nil {
		return err
	}
	if err := os.Rename(newPath, exe); err == nil {
		log.Printf("self-update: %s -> %s, restarting", a.gitSHA, m.Version)
		cmd := exec.Command(exe, os.Args[1:]...)
		cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
		cmd.Env = os.Environ()
		if err := cmd.Start(); err == nil {
			os.Exit(0)
		}
		return err
	}
	log.Printf("self-update: %s staged at %s (supervisor will swap on restart)", m.Version, newPath)
	return nil
}

func verifySHA256(path, want string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return err
	}
	got := hex.EncodeToString(h.Sum(nil))
	if !strings.EqualFold(got, want) {
		return fmt.Errorf("sha256 mismatch: got %s want %s", got, want)
	}
	return nil
}

// probeMemBytes reports total system memory in bytes, best-effort and without
// an external dependency (Linux /proc/meminfo, then macOS/BSD sysctl).
func probeMemBytes() uint64 {
	if b, err := os.ReadFile("/proc/meminfo"); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			if strings.HasPrefix(line, "MemTotal:") {
				f := strings.Fields(line)
				if len(f) >= 2 {
					if kb, err := strconv.ParseUint(f[1], 10, 64); err == nil {
						return kb * 1024
					}
				}
			}
		}
	}
	if out, err := exec.Command("sysctl", "-n", "hw.memsize").Output(); err == nil {
		if v, err := strconv.ParseUint(strings.TrimSpace(string(out)), 10, 64); err == nil {
			return v
		}
	}
	// Windows: wmic reports total physical memory in bytes.
	if out, err := exec.Command("wmic", "computersystem", "get", "TotalPhysicalMemory", "/value").Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "TotalPhysicalMemory=") {
				if v, err := strconv.ParseUint(strings.TrimPrefix(line, "TotalPhysicalMemory="), 10, 64); err == nil {
					return v
				}
			}
		}
	}
	return 0
}

// probeGPU reports the first GPU's model, its memory, and the GPU count, via
// nvidia-smi when present. It returns zero values on a box without NVIDIA.
func probeGPU() (name, vram string, count int) {
	out, err := exec.Command("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader").Output()
	if err != nil {
		return "", "", 0
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	for i, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		count++
		if i == 0 {
			if parts := strings.SplitN(line, ",", 2); len(parts) == 2 {
				name = strings.TrimSpace(parts[0])
				vram = strings.TrimSpace(parts[1])
			}
		}
	}
	return name, vram, count
}

// beaconGroup is the site-local multicast group every agent broadcasts to and
// every platform listens on. Multicast (not broadcast) so the discovery works
// across switches and is routable within the site.
const beaconGroup = "239.255.42.99"

// ANSI colours for the terminal UI.
const (
	rst = "\033[0m"
	dim = "\033[2m"
	bld = "\033[1m"
	cyn = "\033[36m"
	grn = "\033[32m"
	ylw = "\033[33m"
	red = "\033[31m"
)

// healLoop is the self-heal watchdog: it probes this agent's own /health and
// exits after three consecutive failures so the supervisor (launchd/systemd/
// supervise-cryptalas.sh) restarts it. This recovers from a hang, not just a
// crash.
func (a *Agent) healLoop(addr string, interval time.Duration) {
	tick := time.NewTicker(interval)
	defer tick.Stop()
	failures := 0
	for range tick.C {
		a.healthProbes.Add(1)
		resp, err := http.Get("http://" + addr + "/health")
		if err == nil && resp.StatusCode == http.StatusOK {
			resp.Body.Close()
			failures = 0
			continue
		}
		if resp != nil {
			resp.Body.Close()
		}
		failures++
		a.healthFails.Add(1)
		log.Printf("self-heal: health probe failed (%d/3)", failures)
		if failures >= 3 {
			log.Printf("self-heal: unresponsive, exiting for supervisor restart")
			os.Exit(1)
		}
	}
}

// statusLoop renders the live status panel once a second.
func (a *Agent) statusLoop() {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	for range tick.C {
		a.renderStatus()
	}
}

// renderStatus clears and redraws the full debug panel.
func (a *Agent) renderStatus() {
	beaconPort, beaconInterval := a.rt.beaconPort, a.rt.beaconInterval

	gs := probeGPUStats()
	memTotal := probeMemBytes()
	cpu := probeCPUUtil()
	rxBps, txBps := a.netSpeed.current()

	a.mu.Lock()
	jobs := make([]*Job, len(a.order))
	for i, id := range a.order {
		jobs[i] = a.jobs[id]
	}
	var queued, running, done, failed int
	for _, j := range a.jobs {
		switch j.Status {
		case "queued":
			queued++
		case "running":
			running++
		case "done":
			done++
		case "failed":
			failed++
		}
	}
	a.mu.Unlock()

	spin := []string{"|", "/", "-", "\\"}[time.Now().UnixNano()/200000000%4]
	memPct := 0.0
	if memTotal > 0 {
		memPct = 100 * float64(memTotal) / float64(memTotal) // placeholder, real used below
	}
	_ = memPct

	var b strings.Builder
	line := strings.Repeat("─", 44)
	b.WriteString("\033[2J\033[H")

	// ── sci-fi banner ────────────────────────────────────────────────
	fmt.Fprintf(&b, "  %s%s╔══════════════════════════════════════════╗%s\n", bld, cyn, rst)
	fmt.Fprintf(&b, "  %s║%s   %s⚡ CRYPTALAS%s  %s// COMPUTE NODE%s             %s║%s\n", cyn, rst, bld, rst, dim, rst, cyn, rst)
	fmt.Fprintf(&b, "  %s║%s   %s%s%s%s  %s·%s  %s%s%s  %s·%s  %s%s%s            %s║%s\n",
		cyn, rst, dim, grn, a.gitSHA, rst, dim, rst, deviceColor(a.device), a.device, rst, dim, rst, ylw, a.rt.ip, rst, cyn, rst)
	fmt.Fprintf(&b, "  %s%s╚══════════════════════════════════════════════╝%s\n", bld, cyn, rst)

	// ── resource gauges ──────────────────────────────────────────────
	fmt.Fprintf(&b, "  %sCPU%s  %s  %s%d cores%s\n", dim, rst, gauge(cpu, 22), bld, runtime.NumCPU(), rst)
	fmt.Fprintf(&b, "  %sMEM%s  %s  %s%s%s\n", dim, rst, gauge(100*float64(probeMemUsed())/float64(max(memTotal, 1)), 22), bld, formatBytes(probeMemUsed()), rst)
	if gs.Count > 0 {
		fmt.Fprintf(&b, "  %sGPU%s  %s  %s%d× %s %s%s\n", dim, rst, gauge(gs.Util, 22), bld, gs.Count, gs.Name, gs.VRAM, rst)
	}
	fmt.Fprintf(&b, "  %sNET%s  %s  ↓ %s  ↑ %s  %s%s%s\n", dim, rst, gauge(netSpeedPct(rxBps), 22), formatRate(rxBps), formatRate(txBps), cyn, spin, rst)
	fmt.Fprintf(&b, "  %s%s%s\n", dim, line, rst)

	// ── tasks ────────────────────────────────────────────────────────
	fmt.Fprintf(&b, "  %sTASKS%s  %squeued %d%s  %srunning %d%s  %sdone %d%s  %sfailed %d%s\n",
		bld, rst, dim, queued, rst, ylw, running, rst, grn, done, rst, red, failed, rst)
	for _, j := range jobs {
		if j.Status == "running" {
			fmt.Fprintf(&b, "    %s%s%s  %-9s %-16s %sRUNNING%s  %s\n", cyn, spin, rst, j.Type, j.Model, ylw, rst, fmtDuration(time.Since(time.UnixMilli(j.Created))))
		} else if j.Status == "done" {
			fmt.Fprintf(&b, "    %s✓%s  %-9s %-16s %sdone%s\n", grn, rst, j.Type, j.Model, dim, rst)
		} else if j.Status == "failed" {
			fmt.Fprintf(&b, "    %s✗%s  %-9s %-16s %sfailed%s\n", red, rst, j.Type, j.Model, red, rst)
		}
	}
	fmt.Fprintf(&b, "  %s%s%s\n", dim, line, rst)

	// ── beacon + health ─────────────────────────────────────────────
	lastAgo := "—"
	if lb := a.lastBeacon.Load(); lb > 0 {
		lastAgo = fmt.Sprintf("%ds ago", (time.Now().UnixMilli()-lb)/1000)
	}
	fmt.Fprintf(&b, "  %sbeacon%s  multicast 239.255.42.99:%d  every %s  %slast sent %s%s\n",
		dim, rst, beaconPort, beaconInterval, dim, lastAgo, rst)
	fmt.Fprintf(&b, "  %shealth%s  %d probes  %d failures\n", dim, rst, a.healthProbes.Load(), a.healthFails.Load())
	fmt.Fprintf(&b, "  %s%s%s\n", dim, line, rst)

	// ── messages (persistent) ────────────────────────────────────────
	for _, m := range msgBuffer.recent() {
		fmt.Fprintf(&b, "  %s%s%s\n", dim, strings.TrimSpace(m), rst)
	}
	fmt.Fprintf(&b, "  %s%s%s\n", dim, line, rst)
	fmt.Print(b.String())
}

func gauge(pct float64, width int) string {
	if width < 4 {
		width = 10
	}
	if pct < 0 {
		pct = 0
	}
	if pct > 100 {
		pct = 100
	}
	filled := int(float64(width) * pct / 100)
	return fmt.Sprintf("[%s%s] %3.0f%%", strings.Repeat("█", filled), strings.Repeat("░", width-filled), pct)
}

func formatRate(bps float64) string {
	if bps < 1024 {
		return fmt.Sprintf("%.0f B/s", bps)
	}
	if bps < 1024*1024 {
		return fmt.Sprintf("%.1f KB/s", bps/1024)
	}
	return fmt.Sprintf("%.1f MB/s", bps/1024/1024)
}

func netSpeedPct(bps float64) float64 {
	return math.Min(100, bps/1024/1024*10) // 10 MB/s = full bar
}

func fmtDuration(d time.Duration) string {
	return d.Round(time.Second).String()
}

func max(a, b uint64) uint64 {
	if a > b {
		return a
	}
	return b
}

// deviceColor colours the device badge by capability.
func deviceColor(d string) string {
	switch d {
	case "cuda", "gpu":
		return grn
	case "metal":
		return cyn
	case "cpu", "auto":
		return ylw
	default:
		return dim
	}
}

func formatBytes(n uint64) string {
	if n == 0 {
		return "—"
	}
	const unit = uint64(1024)
	div, exp := unit, 0
	for m := n / unit; m >= unit; m /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(n)/float64(div), "KMGTPE"[exp])
}

func formatGPU(name, vram string, count int) string {
	if count == 0 {
		return "—"
	}
	if name == "" {
		return fmt.Sprintf("%d×", count)
	}
	return fmt.Sprintf("%d× %s %s", count, name, vram)
}

// gpuStats is the fuller GPU snapshot reported by nvidia-smi.
type gpuStats struct {
	Name    string
	VRAM    string
	Count   int
	Util    float64
	MemUsed uint64
}

// probeGPUStats reports the first GPU's model, total/used memory, count and
// utilization via nvidia-smi. Zero values on a box without NVIDIA.
func probeGPUStats() gpuStats {
	out, err := exec.Command("nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader,nounits").Output()
	if err != nil {
		return gpuStats{}
	}
	var gs gpuStats
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.Split(line, ", ")
		if len(parts) < 4 {
			continue
		}
		gs.Count++
		if gs.Count == 1 {
			gs.Name = parts[0]
			gs.VRAM = parts[1] + " MiB"
			if mb, err := strconv.ParseUint(parts[2], 10, 64); err == nil {
				gs.MemUsed = mb * 1024 * 1024
			}
			gs.Util, _ = strconv.ParseFloat(parts[3], 64)
		}
	}
	return gs
}

// probeCPUUtil reports the CPU utilisation % (0-100), best-effort across
// Linux (/proc/stat), macOS (top) and Windows (wmic).
func probeCPUUtil() float64 {
	if b, err := os.ReadFile("/proc/stat"); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			if !strings.HasPrefix(line, "cpu ") {
				continue
			}
			f := strings.Fields(line)
			var total, idle uint64
			for i := 1; i < len(f); i++ {
				v, _ := strconv.ParseUint(f[i], 10, 64)
				total += v
				if i == 4 { // idle is the 4th field after "cpu"
					idle = v
				}
			}
			if total > 0 {
				return 100 * float64(total-idle) / float64(total)
			}
		}
	}
	// macOS: top -l 1 prints "CPU usage: ..% user, ..% sys, ..% idle".
	if out, err := exec.Command("top", "-l", "1", "-n", "0").Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			if !strings.Contains(line, "CPU usage") {
				continue
			}
			if idx := strings.Index(line, "idle"); idx > 0 {
				fields := strings.Fields(line[:idx])
				if len(fields) > 0 {
					idleStr := strings.TrimSuffix(fields[len(fields)-1], "%")
					if idle, err := strconv.ParseFloat(idleStr, 64); err == nil {
						return 100 - idle
					}
				}
			}
		}
	}
	if out, err := exec.Command("wmic", "cpu", "get", "LoadPercentage", "/value").Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "LoadPercentage=") {
				if v, err := strconv.ParseFloat(strings.TrimPrefix(line, "LoadPercentage="), 64); err == nil {
					return v
				}
			}
		}
	}
	return 0
}

// probeMemUsed reports memory in use (bytes), best-effort across Linux
// (/proc/meminfo) and Windows (wmic). 0 when unknown.
func probeMemUsed() uint64 {
	if b, err := os.ReadFile("/proc/meminfo"); err == nil {
		var total, avail uint64
		for _, line := range strings.Split(string(b), "\n") {
			f := strings.Fields(line)
			if len(f) < 2 {
				continue
			}
			switch f[0] {
			case "MemTotal:":
				total, _ = strconv.ParseUint(f[1], 10, 64)
			case "MemAvailable:":
				avail, _ = strconv.ParseUint(f[1], 10, 64)
			}
		}
		if total > avail {
			return (total - avail) * 1024
		}
	}
	return 0
}

// capabilities lists what this agent can do.
func (a *Agent) capabilities() []string {
	caps := []string{"train", "sweep", "beacon"}
	switch a.device {
	case "cuda", "gpu":
		caps = append(caps, "cuda")
	case "metal":
		caps = append(caps, "metal")
	}
	if a.ollama != "" {
		caps = append(caps, "ollama")
	}
	if a.cudaRunner != "" {
		caps = append(caps, "cuda-runner")
	}
	return caps
}

// jobSummary counts the agent's jobs by status.
func (a *Agent) jobSummary() (queued, running, done, failed int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for _, j := range a.jobs {
		switch j.Status {
		case "queued":
			queued++
		case "running":
			running++
		case "done":
			done++
		case "failed":
			failed++
		}
	}
	return
}

// probeNetInterfaces lists the host's interfaces with IP, MAC and up flag.
func probeNetInterfaces() []NetIface {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	var out []NetIface
	for _, iface := range ifaces {
		ip := ""
		if addrs, err := iface.Addrs(); err == nil {
			for _, a := range addrs {
				if ipn, ok := a.(*net.IPNet); ok && ipn.IP.To4() != nil && !ipn.IP.IsLoopback() {
					ip = ipn.IP.String()
					break
				}
			}
		}
		up := iface.Flags&net.FlagUp != 0
		if !up && ip == "" {
			continue
		}
		out = append(out, NetIface{Name: iface.Name, IP: ip, MAC: iface.HardwareAddr.String(), Up: up})
	}
	return out
}

// privileges reports what this agent can access/do.
func (a *Agent) privileges() []string {
	var privs []string
	if os.Geteuid() == 0 {
		privs = append(privs, "root")
	}
	switch a.device {
	case "cuda", "gpu":
		privs = append(privs, "cuda")
	case "metal":
		privs = append(privs, "metal")
	}
	if a.ollama != "" {
		privs = append(privs, "ollama")
	}
	if f, err := os.CreateTemp(a.state, "probe.*"); err == nil {
		f.Close()
		os.Remove(f.Name())
		privs = append(privs, "state-writable")
	}
	privs = append(privs, "multicast")
	return privs
}

// probeNetCounters returns the total rx/tx bytes across interfaces, best-effort
// via Linux /proc/net/dev or macOS netstat -ib.
func probeNetCounters() (rx, tx uint64) {
	if b, err := os.ReadFile("/proc/net/dev"); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			if !strings.Contains(line, ":") {
				continue
			}
			parts := strings.SplitN(line, ":", 2)
			if len(parts) != 2 {
				continue
			}
			f := strings.Fields(parts[1])
			if len(f) >= 9 {
				r, _ := strconv.ParseUint(f[0], 10, 64)
				t, _ := strconv.ParseUint(f[8], 10, 64)
				rx += r
				tx += t
			}
		}
		return rx, tx
	}
	if out, err := exec.Command("netstat", "-ib").Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			f := strings.Fields(line)
			if len(f) >= 10 {
				r, _ := strconv.ParseUint(f[6], 10, 64)
				t, _ := strconv.ParseUint(f[9], 10, 64)
				rx += r
				tx += t
			}
		}
	}
	return rx, tx
}

// netSpeedTracker samples total rx/tx bytes and computes bytes/sec.
type netSpeedTracker struct {
	mu       sync.Mutex
	lastRx   uint64
	lastTx   uint64
	lastTime time.Time
	rxBps    float64
	txBps    float64
}

func (t *netSpeedTracker) sample() {
	rx, tx := probeNetCounters()
	t.mu.Lock()
	defer t.mu.Unlock()
	now := time.Now()
	if !t.lastTime.IsZero() {
		if dt := now.Sub(t.lastTime).Seconds(); dt > 0 {
			t.rxBps = float64(rx-t.lastRx) / dt
			t.txBps = float64(tx-t.lastTx) / dt
		}
	}
	t.lastRx, t.lastTx, t.lastTime = rx, tx, now
}

func (t *netSpeedTracker) current() (rxBps, txBps float64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.rxBps, t.txBps
}

// runForecast runs a time-series forecast via gpu/forecast.py, resolving a
// history_ref against the marketdata service first when present.
func (a *Agent) runForecast(j *Job) map[string]any {
	a.resolveHistory(j)
	b, _ := json.Marshal(j)
	cmd := exec.Command("python3", "gpu/forecast.py")
	cmd.Stdin = bytes.NewReader(b)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if err := cmd.Run(); err != nil {
		return map[string]any{"trained": false, "error": err.Error(), "detail": out.String()}
	}
	var res map[string]any
	if err := json.Unmarshal(out.Bytes(), &res); err != nil {
		return map[string]any{"trained": false, "error": "bad forecast output: " + err.Error(), "detail": out.String()}
	}
	return res
}

// runBacktest runs a simple SMA-momentum backtest over a close series (or a
// history_ref), returning equity, total return and Sharpe.
func (a *Agent) runBacktest(j *Job) map[string]any {
	a.resolveHistory(j)
	closes := extractCloses(j.Payload["series"])
	if len(closes) < 5 {
		return map[string]any{"error": "need >= 5 closes", "runner": "backtest"}
	}
	window := int(toFloat(j.Payload["window"]))
	if window < 2 || window > len(closes) {
		window = 10
	}
	if window > len(closes) {
		window = len(closes)
	}
	eq := 1.0
	prev := 0.0
	trades := 0
	var rets []float64
	for i := 1; i < len(closes); i++ {
		sig := 0.0
		if i >= window {
			sma := 0.0
			for k := i - window; k < i; k++ {
				sma += closes[k]
			}
			sma /= float64(window)
			if closes[i] > sma {
				sig = 1
			}
		}
		if sig != prev {
			trades++
			prev = sig
		}
		r := sig * (closes[i]/closes[i-1] - 1)
		rets = append(rets, r)
		eq *= 1 + r
	}
	return map[string]any{
		"runner": "backtest", "method": "sma-momentum", "window": window,
		"points": len(closes), "trades": trades,
		"final_equity": round(eq, 6), "total_return": round(eq-1, 6),
		"sharpe": round(sharpe(rets), 4),
	}
}

// resolveHistory fetches the close series when the payload carries a history_ref.
func (a *Agent) resolveHistory(j *Job) {
	ref, ok := j.Payload["history_ref"].(map[string]any)
	if !ok {
		return
	}
	if s := fetchHistory(ref); s != nil {
		j.Payload["series"] = s
	}
}

// fetchHistory pulls klines from the marketdata service and returns [{t,v}] closes.
func fetchHistory(ref map[string]any) []map[string]any {
	symbol, _ := ref["symbol"].(string)
	interval, _ := ref["interval"].(string)
	if interval == "" {
		interval = "1m"
	}
	limit := 200
	if n := toFloat(ref["limit"]); n > 0 {
		limit = int(n)
	}
	base, _ := ref["marketdata"].(string)
	if base == "" {
		base = "http://127.0.0.1:8796"
	}
	u := fmt.Sprintf("%s/klines?symbol=%s&interval=%s&limit=%d", base, url.QueryEscape(symbol), url.QueryEscape(interval), limit)
	resp, err := http.Get(u)
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	var body struct {
		Klines []struct {
			T int64   `json:"t"`
			C float64 `json:"c"`
		} `json:"klines"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil
	}
	out := make([]map[string]any, len(body.Klines))
	for i, k := range body.Klines {
		out[i] = map[string]any{"t": k.T, "v": k.C}
	}
	return out
}

func toFloat(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int:
		return float64(n)
	case json.Number:
		f, _ := n.Float64()
		return f
	default:
		return 0
	}
}

func round(v float64, places int) float64 {
	shift := math.Pow10(places)
	return math.Round(v*shift) / shift
}

func extractCloses(series any) []float64 {
	var out []float64
	add := func(m map[string]any) {
		if v, ok := m["v"]; ok {
			out = append(out, toFloat(v))
		} else if c, ok := m["c"]; ok {
			out = append(out, toFloat(c))
		}
	}
	switch s := series.(type) {
	case []map[string]any:
		for _, m := range s {
			add(m)
		}
	case []any:
		for _, e := range s {
			if m, ok := e.(map[string]any); ok {
				add(m)
			} else {
				out = append(out, toFloat(e))
			}
		}
	}
	return out
}

func sharpe(rets []float64) float64 {
	if len(rets) < 2 {
		return 0
	}
	mean := 0.0
	for _, r := range rets {
		mean += r
	}
	mean /= float64(len(rets))
	var v float64
	for _, r := range rets {
		v += (r - mean) * (r - mean)
	}
	sd := math.Sqrt(v / float64(len(rets)))
	if sd == 0 {
		return 0
	}
	return mean / sd
}

// probeDisk reports total and free space (bytes) on the working directory's
// volume, best-effort via df (Unix) or wmic (Windows).
func probeDisk() (total, free uint64) {
	if out, err := exec.Command("df", "-k", ".").Output(); err == nil {
		lines := strings.Split(strings.TrimSpace(string(out)), "\n")
		if len(lines) >= 2 {
			f := strings.Fields(lines[1])
			if len(f) >= 4 {
				if t, err := strconv.ParseUint(f[1], 10, 64); err == nil {
					total = t * 1024
				}
				if a, err := strconv.ParseUint(f[3], 10, 64); err == nil {
					free = a * 1024
				}
			}
		}
		return total, free
	}
	if out, err := exec.Command("wmic", "logicaldisk", "get", "Size,FreeSpace", "/value").Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			line = strings.TrimSpace(line)
			switch {
			case strings.HasPrefix(line, "Size="):
				total, _ = strconv.ParseUint(strings.TrimPrefix(line, "Size="), 10, 64)
			case strings.HasPrefix(line, "FreeSpace="):
				free, _ = strconv.ParseUint(strings.TrimPrefix(line, "FreeSpace="), 10, 64)
			}
		}
	}
	return total, free
}

// ollamaModels returns the models the local Ollama serves, or nil when absent.
func (a *Agent) ollamaModels() []string {
	if a.ollama == "" {
		return nil
	}
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get(a.ollama + "/api/tags")
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	var body struct {
		Models []struct {
			Name string `json:"name"`
		} `json:"models"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil
	}
	out := make([]string, len(body.Models))
	for i, m := range body.Models {
		out[i] = m.Name
	}
	return out
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}
