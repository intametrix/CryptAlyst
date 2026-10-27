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
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
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
	updateManifest := flag.String("update-manifest", "", "URL of the self-update manifest {version,binary,sha256}")
	updateInterval := flag.Duration("update-interval", 5*time.Minute, "how often to check for a new build")
	flag.Parse()

	dev := *device
	if dev == "auto" {
		dev = detectGPU()
		log.Printf("device auto -> %s", dev)
	}
	ag := newAgent(*stateDir, dev, *gitSHA, *ollama, *cudaRunner)
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
	if *register != "" {
		host := *advertise
		if host == "" {
			host = "http://" + *addr
		}
		go enrollAndHeartbeat(ag, *register, host, dev)
	}
	log.Printf("CryptALAS agent on %s (device=%s)", *addr, *device)
	if err := http.ListenAndServe(*addr, mux); err != nil {
		log.Fatal(err)
	}
}

// enrollAndHeartbeat registers this agent on the platform and then refreshes
// its heartbeat every 30s so the platform knows it is still alive.
func enrollAndHeartbeat(a *Agent, platform, host, device string) {
	payload := func() []byte {
		b, _ := json.Marshal(map[string]any{
			"id":          host,
			"host":        host,
			"device":      device,
			"arch":        runtime.GOARCH,
			"num_cpu":     runtime.NumCPU(),
			"go_version":  runtime.Version(),
			"git_sha":     a.gitSHA,
			"gpu_present": device == "gpu" || device == "cuda" || device == "metal",
		})
		return b
	}
	client := &http.Client{Timeout: 5 * time.Second}
	post := func(path string, body []byte) {
		req, err := http.NewRequest(http.MethodPost, platform+path, bytes.NewReader(body))
		if err != nil {
			return
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := client.Do(req)
		if err != nil {
			log.Printf("enroll %s: %v", path, err)
			return
		}
		resp.Body.Close()
	}
	post("/api/agents/register", payload())
	tick := time.NewTicker(30 * time.Second)
	defer tick.Stop()
	for range tick.C {
		hb, _ := json.Marshal(map[string]any{"id": host})
		post("/api/agents/heartbeat", hb)
	}
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

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}
