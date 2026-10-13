package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

func validID(id string) bool { b, e := hex.DecodeString(id); return e == nil && len(b) == 16 }
func saveReference(r *http.Request, folder string) error {
	seen := map[string]bool{}
	midi, mp3 := 0, 0
	files := r.MultipartForm.File["reference"]
	if len(files) > 32 {
		return fmt.Errorf("Select at most 32 reference files")
	}
	for _, header := range files {
		name := filepath.Base(strings.ReplaceAll(header.Filename, "\\", "/"))
		ext := strings.ToLower(filepath.Ext(name))
		stem := strings.TrimSuffix(strings.ToLower(name), ext)
		if ext != ".mp3" && ext != ".mid" {
			continue
		}
		if stem == "preview" {
			continue
		}
		if seen[strings.ToLower(name)] {
			return fmt.Errorf("Duplicate filename in reference folder")
		}
		seen[strings.ToLower(name)] = true
		if ext == ".mid" {
			midi++
		} else {
			mp3++
		}
		src, e := header.Open()
		if e != nil {
			return fmt.Errorf("Cannot read reference file")
		}
		dst, e := os.OpenFile(filepath.Join(folder, name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if e != nil {
			src.Close()
			return fmt.Errorf("Cannot save reference file")
		}
		n, copyErr := io.Copy(dst, io.LimitReader(src, (100<<20)+1))
		src.Close()
		closeErr := dst.Close()
		if copyErr != nil || closeErr != nil || n == 0 || n > 100<<20 {
			return fmt.Errorf("Each reference file must be nonempty and under 100 MB")
		}
	}
	if midi != 1 || mp3 < 1 {
		return fmt.Errorf("Select exactly one .mid file and at least one MP3 (preview.mp3 is ignored)")
	}
	return nil
}
func (a *API) approve(w http.ResponseWriter, r *http.Request) {
	var approval struct {
		Budget float64 `json:"budget_usd"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, 1024)
	if err := json.NewDecoder(r.Body).Decode(&approval); err != nil || math.IsNaN(approval.Budget) || math.IsInf(approval.Budget, 0) || approval.Budget <= 0 {
		fail(w, 400, "Enter a positive spending limit in USD")
		return
	}
	id := r.PathValue("id")
	if !validID(id) {
		fail(w, 404, "Job not found")
		return
	}
	a.mu.Lock()
	job, ok := a.jobs[id]
	if !ok || job.Status != "awaiting_approval" {
		a.mu.Unlock()
		fail(w, 409, "Job is not awaiting approval")
		return
	}
	if exceeded, _ := job.Report["size_exceeded"].(bool); exceeded {
		a.mu.Unlock()
		fail(w, 400, "Context is too large. Shorten durations and recalculate first.")
		return
	}
	estimate, ok := job.Report["estimated_cost_usd"].(float64)
	if !ok || approval.Budget < estimate {
		a.mu.Unlock()
		fail(w, 400, "Estimated cost exceeds your spending limit")
		return
	}
	select {
	case a.slot <- struct{}{}:
	default:
		a.mu.Unlock()
		fail(w, 409, "Another job is running")
		return
	}
	dir := filepath.Join(a.workspace, id)
	path := filepath.Join(dir, "request.json")
	data, err := os.ReadFile(path)
	var request Request
	if err == nil {
		err = json.Unmarshal(data, &request)
	}
	if err != nil {
		<-a.slot
		a.mu.Unlock()
		fail(w, 500, "Cannot resume job")
		return
	}
	job.Report["approved_budget_usd"] = approval.Budget
	estimateData, _ := json.Marshal(job.Report)
	if err = os.WriteFile(filepath.Join(dir, "estimate.json"), estimateData, 0600); err != nil {
		<-a.slot
		a.mu.Unlock()
		fail(w, 500, "Cannot save spending limit")
		return
	}
	request.Stage = "generate"
	request.Result = filepath.Join(dir, "result.json")
	data, _ = json.Marshal(request)
	if err = os.WriteFile(path, data, 0600); err != nil {
		<-a.slot
		a.mu.Unlock()
		fail(w, 500, "Cannot save approval")
		return
	}
	job.Status = "queued"
	job.Report = nil
	a.jobs[id] = job
	a.mu.Unlock()
	a.wg.Add(1)
	go a.process(id, dir, path, request.Result)
	respond(w, 202, job)
}
func (a *API) preview(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	job, ok := a.get(id)
	if !validID(id) || !ok || (job.Status != "awaiting_approval" && job.Status != "completed") {
		fail(w, 404, "Preview not available")
		return
	}
	w.Header().Set("Content-Type", "audio/wav")
	http.ServeFile(w, r, filepath.Join(a.workspace, id, "reference_mix.wav"))
}
func (a *API) references(w http.ResponseWriter, r *http.Request) {
	entries, _ := os.ReadDir(filepath.Join(a.root, "user_references"))
	result := []map[string]any{}
	for _, entry := range entries {
		if !entry.IsDir() || !validID(entry.Name()) {
			continue
		}
		files, _ := os.ReadDir(filepath.Join(a.root, "user_references", entry.Name()))
		names := []string{}
		for _, file := range files {
			names = append(names, file.Name())
		}
		result = append(result, map[string]any{"id": entry.Name(), "files": names})
	}
	respond(w, 200, result)
}

func (a *API) cancel(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !validID(id) {
		fail(w, 404, "Job not found")
		return
	}
	a.mu.Lock()
	job, ok := a.jobs[id]
	if !ok || job.Status != "awaiting_approval" {
		a.mu.Unlock()
		fail(w, 409, "Only prepared jobs can be cancelled before sending")
		return
	}
	job.Status = "cancelled"
	job.Report = nil
	a.jobs[id] = job
	a.mu.Unlock()
	os.RemoveAll(filepath.Join(a.workspace, id))
	respond(w, 200, job)
}

func (a *API) recalculate(w http.ResponseWriter, r *http.Request) {
	var settings struct {
		ReferenceSeconds int `json:"reference_seconds"`
		Seconds          int `json:"seconds"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, 1024)
	if err := json.NewDecoder(r.Body).Decode(&settings); err != nil || settings.ReferenceSeconds < 0 || settings.ReferenceSeconds > 1800 || settings.Seconds < 0 || settings.Seconds > 900 {
		fail(w, 400, "Invalid durations")
		return
	}
	id := r.PathValue("id")
	if !validID(id) {
		fail(w, 404, "Job not found")
		return
	}
	a.mu.Lock()
	job, ok := a.jobs[id]
	if !ok || job.Status != "awaiting_approval" {
		a.mu.Unlock()
		fail(w, 409, "Only a prepared estimate can be recalculated")
		return
	}
	select {
	case a.slot <- struct{}{}:
	default:
		a.mu.Unlock()
		fail(w, 409, "Another job is running")
		return
	}
	dir := filepath.Join(a.workspace, id)
	path := filepath.Join(dir, "request.json")
	data, err := os.ReadFile(path)
	var request Request
	if err == nil {
		err = json.Unmarshal(data, &request)
	}
	if err != nil {
		<-a.slot
		a.mu.Unlock()
		fail(w, 500, "Cannot load preparation")
		return
	}
	request.ReferenceSeconds = settings.ReferenceSeconds
	request.Seconds = settings.Seconds
	request.Stage = "prepare"
	request.Result = filepath.Join(dir, "estimate.json")
	data, _ = json.Marshal(request)
	if err = os.WriteFile(path, data, 0600); err != nil {
		<-a.slot
		a.mu.Unlock()
		fail(w, 500, "Cannot update preparation")
		return
	}
	job.Status = "queued"
	job.Report = nil
	a.jobs[id] = job
	a.mu.Unlock()
	a.wg.Add(1)
	go a.process(id, dir, path, request.Result)
	respond(w, 202, job)
}
