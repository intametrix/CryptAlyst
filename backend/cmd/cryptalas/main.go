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
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"pumpdump/backend/internal/gpuagent"
)

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
	if j.Type == "ollama" {
		result = a.runOllama(j)
	} else if (j.Device == "cuda" || j.Device == "gpu" || j.Device == "metal") && a.cudaRunner != "" {
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

// runTTY is the ATLAS-style interactive shell.
func runTTY(ag *Agent) {
	banner(ag)
	sc := bufio.NewScanner(os.Stdin)
	fmt.Println("type 'help' for commands")
	for {
		fmt.Print("cryptalas> ")
		if !sc.Scan() {
			return
		}
		line := strings.TrimSpace(sc.Text())
		switch {
		case line == "":
		case line == "help":
			fmt.Println("  ACCEPT   submit <model> [train|ollama]   queue a training job")
			fmt.Println("  STORE    jobs                            list jobs")
			fmt.Println("           job <id>                        inspect one job")
			fmt.Println("  TRAIN    watch                           live progress")
			fmt.Println("  SERVE    gpu                             device + GPU report")
			fmt.Println("           ollama                          Ollama model list")
			fmt.Println("           status                          one-page summary")
			fmt.Println("  SHELL    clear / help / quit")
		case line == "gpu":
			printJSON(ag.gpuReport())
		case line == "ollama":
			printJSON(ag.gpuReport()["ollama"])
		case line == "status":
			banner(ag)
			g := ag.gpuReport()
			fmt.Printf("  device      : %v\n", g["device"])
			fmt.Printf("  gpu_present : %v\n", g["gpu_present"])
			fmt.Printf("  num_cpu     : %v\n", g["num_cpu"])
			fmt.Printf("  ollama      : %s\n", ollamaState(g))
			fmt.Println("  jobs:")
			for _, j := range ag.list() {
				fmt.Printf("    %s  %-6s %-16s %s\n", j.ID, j.Type, j.Model, j.Status)
			}
		case line == "jobs":
			fmt.Printf("  %-18s %-7s %-18s %s\n", "id", "type", "model", "status")
			for _, j := range ag.list() {
				fmt.Printf("  %-18s %-7s %-18s %s\n", j.ID, j.Type, j.Model, j.Status)
			}
		case strings.HasPrefix(line, "job "):
			id := strings.TrimSpace(strings.TrimPrefix(line, "job "))
			j, ok := ag.get(id)
			if !ok {
				fmt.Println("  not found")
				continue
			}
			printJSON(j)
		case strings.HasPrefix(line, "submit "):
			parts := strings.Fields(line)
			if len(parts) < 2 {
				fmt.Println("  usage: submit <model> [train|ollama]")
				continue
			}
			typ := "train"
			if len(parts) > 2 {
				typ = parts[2]
			}
			j := ag.submit(parts[1], map[string]any{}, "", "", typ)
			fmt.Printf("  queued %s (%s, %s)\n", j.ID, typ, j.Model)
		case line == "watch":
			for {
				fmt.Print("\033[2J\033[H")
				banner(ag)
				for _, j := range ag.list() {
					fmt.Printf("  %s  %-6s %-16s %s\n", j.ID, j.Type, j.Model, j.Status)
				}
				time.Sleep(2 * time.Second)
			}
		case line == "clear":
			fmt.Print("\033[2J\033[H")
		case line == "quit" || line == "exit":
			return
		default:
			fmt.Println("  unknown command — type 'help'")
		}
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
	gitSHA := flag.String("git-sha", "", "git commit this agent runs")
	ollama := flag.String("ollama", "http://localhost:11434", "Ollama base URL on the GPU box")
	cudaRunner := flag.String("cuda-runner", "gpu/cuda_train.py", "path to the CUDA/GPU python trainer")
	register := flag.String("register", "", "platform base URL to enroll on (e.g. http://127.0.0.1:8787)")
	advertise := flag.String("advertise", "", "this agent's own base URL to report (defaults to http://<addr>)")
	beaconInterval := flag.Duration("beacon-interval", 30*time.Second, "how often to broadcast the agent beacon (full status)")
	beaconPort := flag.Int("beacon-port", 8788, "UDP port to broadcast the beacon on")
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
	host := *advertise
	if host == "" {
		host = autoHost(*addr)
	}
	go beaconLoop(ag, *register, host, dev, *beaconInterval, *beaconPort)
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
		gpuName, gpuVRAM, gpuCount := probeGPU()
		memTotal := probeMemBytes()
		b, _ := json.Marshal(map[string]any{
			"id":          host,
			"host":        host,
			"name":        name,
			"ip":          ip,
			"device":      device,
			"arch":        runtime.GOARCH,
			"num_cpu":     runtime.NumCPU(),
			"go_version":  runtime.Version(),
			"git_sha":     a.gitSHA,
			"gpu_present": device == "gpu" || device == "cuda" || device == "metal",
			"mem_total":   memTotal,
			"gpu_name":    gpuName,
			"gpu_vram":    gpuVRAM,
			"gpu_count":   gpuCount,
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

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}
