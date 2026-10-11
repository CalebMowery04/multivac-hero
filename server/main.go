package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

type Job struct {
	ID     string         `json:"id"`
	Status string         `json:"status"`
	Error  string         `json:"error,omitempty"`
	Report map[string]any `json:"report,omitempty"`
	Zip    string         `json:"-"`
}
type Request struct {
	Audio      string `json:"audio"`
	References string `json:"references"`
	Output     string `json:"output"`
	Result     string `json:"result"`
	Title      string `json:"title"`
	Artist     string `json:"artist"`
	Seconds    int    `json:"seconds"`
	Mode       string `json:"mode"`
	Model      string `json:"model"`
}
type Runner func(context.Context, string) error
type API struct {
	mu              sync.Mutex
	jobs            map[string]Job
	slot            chan struct{}
	root, workspace string
	run             Runner
	ctx             context.Context
	wg              sync.WaitGroup
}

func respond(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
func fail(w http.ResponseWriter, status int, message string) {
	respond(w, status, map[string]string{"error": message})
}
func (a *API) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, r *http.Request) {
		respond(w, 200, map[string]bool{"ok": true, "api_key_configured": os.Getenv("OPENAI_API_KEY") != ""})
	})
	mux.HandleFunc("POST /api/jobs", a.create)
	mux.HandleFunc("GET /api/jobs/{id}", a.status)
	mux.HandleFunc("GET /api/jobs/{id}/download", a.download)
	files := http.FileServer(http.Dir(filepath.Join(a.root, "web", "dist")))
	mux.Handle("/", files)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Local-only API: reject cross-origin browser mutations, including DNS rebinding.
		host := strings.Split(r.Host, ":")[0]
		if host != "localhost" && host != "127.0.0.1" {
			fail(w, 403, "Local requests only")
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != "http://"+r.Host && origin != "http://localhost:5173" && origin != "http://127.0.0.1:5173" {
			fail(w, 403, "Origin not allowed")
			return
		}
		w.Header().Set("X-Content-Type-Options", "nosniff")
		mux.ServeHTTP(w, r)
	})
}
func (a *API) create(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 100<<20)
	if err := r.ParseMultipartForm(8 << 20); err != nil {
		if r.MultipartForm != nil {
			r.MultipartForm.RemoveAll()
		}
		fail(w, 400, "Upload must be a multipart request under 100 MB")
		return
	}
	defer r.MultipartForm.RemoveAll()
	mode := r.FormValue("mode")
	if mode != "baseline" && mode != "llm" && mode != "audio" {
		fail(w, 400, "Invalid generation mode")
		return
	}
	model := r.FormValue("model")
	if model == "" {
		model = "gpt-4o-mini"
	}
	if model != "gpt-4o-mini" && model != "gpt-4.1-mini" && model != "gpt-4.1-nano" {
		fail(w, 400, "Invalid model")
		return
	}
	seconds, err := strconv.Atoi(r.FormValue("seconds"))
	if err != nil || seconds < 0 || seconds > 900 {
		fail(w, 400, "Seconds must be between 0 and 900")
		return
	}
	if mode == "audio" && (seconds == 0 || seconds > 15) {
		seconds = 15
	}
	title, artist := strings.TrimSpace(r.FormValue("title")), strings.TrimSpace(r.FormValue("artist"))
	if title == "" {
		title = "Generated Song"
	}
	if artist == "" {
		artist = "Unknown"
	}
	if len(title) > 200 || len(artist) > 200 {
		fail(w, 400, "Title and artist must be under 200 bytes")
		return
	}
	file, header, err := r.FormFile("audio")
	if err != nil {
		fail(w, 400, "Audio file required")
		return
	}
	defer file.Close()
	ext := strings.ToLower(filepath.Ext(header.Filename))
	if ext != ".mp3" && ext != ".wav" && ext != ".ogg" && ext != ".flac" {
		fail(w, 400, "Use MP3, WAV, OGG, or FLAC")
		return
	}
	select {
	case a.slot <- struct{}{}:
	default:
		fail(w, 409, "Another chart is generating. Try again when it finishes.")
		return
	}
	started := false
	defer func() {
		if !started {
			<-a.slot
		}
	}()
	idBytes := make([]byte, 16)
	if _, err = rand.Read(idBytes); err != nil {
		fail(w, 500, "Cannot create job")
		return
	}
	id := hex.EncodeToString(idBytes)
	dir := filepath.Join(a.workspace, id)
	if err = os.Mkdir(dir, 0700); err != nil {
		fail(w, 500, "Cannot create job folder")
		return
	}
	defer func() {
		if !started {
			os.RemoveAll(dir)
		}
	}()
	audio := filepath.Join(dir, "input"+ext)
	dst, err := os.OpenFile(audio, os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		fail(w, 500, "Cannot save upload")
		return
	}
	n, copyErr := io.Copy(dst, file)
	closeErr := dst.Close()
	if copyErr != nil || closeErr != nil || n == 0 {
		fail(w, 400, "Cannot read audio upload")
		return
	}
	request := Request{audio, filepath.Join(a.root, "references"), filepath.Join(dir, "output"), filepath.Join(dir, "result.json"), title, artist, seconds, mode, model}
	data, _ := json.Marshal(request)
	requestPath := filepath.Join(dir, "request.json")
	if err = os.WriteFile(requestPath, data, 0600); err != nil {
		fail(w, 500, "Cannot save job")
		return
	}
	a.mu.Lock()
	a.jobs[id] = Job{ID: id, Status: "queued"}
	a.mu.Unlock()
	started = true
	a.wg.Add(1)
	go a.process(id, dir, requestPath, request.Result)
	respond(w, 202, Job{ID: id, Status: "queued"})
}
func (a *API) process(id, dir, requestPath, resultPath string) {
	defer a.wg.Done()
	defer func() { <-a.slot }()
	defer os.Remove(filepath.Join(dir, "input"+filepath.Ext(readAudio(requestPath))))
	a.mu.Lock()
	a.jobs[id] = Job{ID: id, Status: "running"}
	a.mu.Unlock()
	parent := a.ctx
	if parent == nil {
		parent = context.Background()
	}
	ctx, cancel := context.WithTimeout(parent, 30*time.Minute)
	defer cancel()
	err := a.run(ctx, requestPath)
	job := Job{ID: id, Status: "failed", Error: "Generation failed. Try another recording or a shorter clip."}
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		job.Error = "Generation timed out. Try a shorter clip."
	}
	if err == nil {
		data, readErr := os.ReadFile(resultPath)
		var report map[string]any
		if readErr == nil && json.Unmarshal(data, &report) == nil {
			zip, _ := report["zip_path"].(string)
			rel, relErr := filepath.Rel(filepath.Join(dir, "output"), zip)
			info, statErr := os.Stat(zip)
			if relErr == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator)) && statErr == nil && info.Mode().IsRegular() && filepath.Ext(zip) == ".zip" {
				delete(report, "zip_path")
				delete(report, "folder")
				job = Job{ID: id, Status: "completed", Report: report, Zip: zip}
			}
		}
	}
	if job.Status == "failed" {
		log.Printf("Job %s failed (details withheld from browser)", id)
		os.RemoveAll(dir)
	}
	a.mu.Lock()
	a.jobs[id] = job
	a.mu.Unlock()
}
func readAudio(path string) string {
	data, _ := os.ReadFile(path)
	var r Request
	json.Unmarshal(data, &r)
	return r.Audio
}
func (a *API) get(id string) (Job, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	j, ok := a.jobs[id]
	return j, ok
}
func (a *API) status(w http.ResponseWriter, r *http.Request) {
	job, ok := a.get(r.PathValue("id"))
	if !ok {
		fail(w, 404, "Job not found. The API may have restarted.")
		return
	}
	respond(w, 200, job)
}
func (a *API) download(w http.ResponseWriter, r *http.Request) {
	job, ok := a.get(r.PathValue("id"))
	if !ok {
		fail(w, 404, "Job not found")
		return
	}
	if job.Status != "completed" {
		fail(w, 409, "Chart is not ready")
		return
	}
	w.Header().Set("Content-Disposition", `attachment; filename="multivac-hero-song.zip"`)
	w.Header().Set("Content-Type", "application/zip")
	http.ServeFile(w, r, job.Zip)
}
func main() {
	rootFlag := flag.String("root", "..", "Python project root")
	port := flag.Int("port", 8080, "Local port")
	flag.Parse()
	root, err := filepath.Abs(*rootFlag)
	if err != nil {
		log.Fatal(err)
	}
	python := os.Getenv("MULTIVAC_PYTHON")
	if python == "" {
		python = filepath.Join(root, ".venv", "bin", "python")
	}
	if _, err = os.Stat(python); err != nil {
		log.Fatal("Python not found; configure MULTIVAC_PYTHON or install .venv dependencies")
	}
	workspace, err := os.MkdirTemp("", "multivac-jobs-")
	if err != nil {
		log.Fatal(err)
	}
	defer os.RemoveAll(workspace)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	api := &API{ctx: ctx, jobs: make(map[string]Job), slot: make(chan struct{}, 1), root: root, workspace: workspace}
	api.run = func(ctx context.Context, requestPath string) error {
		cmd := exec.CommandContext(ctx, python, filepath.Join(root, "worker.py"), requestPath)
		cmd.Dir = root
		// Library output is discarded: reports cross the boundary in a dedicated JSON file.
		return cmd.Run()
	}
	server := &http.Server{Addr: fmt.Sprintf("127.0.0.1:%d", *port), Handler: api.handler(), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 2 * time.Minute, IdleTimeout: 60 * time.Second}
	log.Printf("Multivac Hero: http://localhost:%d (jobs are session-only)", *port)
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		server.Shutdown(shutdown)
	}()
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Print(err)
	}
	api.wg.Wait()
}
