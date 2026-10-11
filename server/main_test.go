package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testAPI(t *testing.T, run Runner) *API {
	t.Helper()
	return &API{jobs: map[string]Job{}, slot: make(chan struct{}, 1), root: t.TempDir(), workspace: t.TempDir(), run: run}
}
func upload(t *testing.T, a *API, mode, filename string) *httptest.ResponseRecorder {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	for k, v := range map[string]string{"mode": mode, "seconds": "15", "model": "gpt-4o-mini", "title": "Example"} {
		writer.WriteField(k, v)
	}
	part, _ := writer.CreateFormFile("audio", filename)
	part.Write([]byte("audio fixture"))
	writer.Close()
	req := httptest.NewRequest("POST", "http://localhost/api/jobs", &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	w := httptest.NewRecorder()
	a.handler().ServeHTTP(w, req)
	return w
}
func waitJob(t *testing.T, a *API, id string) Job {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		j, _ := a.get(id)
		if j.Status == "completed" || j.Status == "failed" {
			return j
		}
		time.Sleep(time.Millisecond * 5)
	}
	t.Fatal("Job did not finish")
	return Job{}
}
func TestLifecycle(t *testing.T) {
	a := testAPI(t, func(ctx context.Context, path string) error {
		data, _ := os.ReadFile(path)
		var r Request
		json.Unmarshal(data, &r)
		if r.Mode != "audio" || r.Seconds != 15 {
			return errors.New("bad request")
		}
		os.MkdirAll(r.Output, 0700)
		zip := filepath.Join(r.Output, "song.zip")
		os.WriteFile(zip, []byte("zip fixture"), 0600)
		result, _ := json.Marshal(map[string]any{"zip_path": zip, "folder": r.Output, "mode": "llm"})
		return os.WriteFile(r.Result, result, 0600)
	})
	w := upload(t, a, "audio", "../../track.wav")
	if w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	var created Job
	json.Unmarshal(w.Body.Bytes(), &created)
	job := waitJob(t, a, created.ID)
	if job.Status != "completed" {
		t.Fatal(job)
	}
	if job.Report["zip_path"] != nil || job.Report["folder"] != nil {
		t.Fatal("Private paths exposed")
	}
	for _, route := range []string{"/api/jobs/" + job.ID, "/api/jobs/" + job.ID + "/download"} {
		rr := httptest.NewRecorder()
		a.handler().ServeHTTP(rr, httptest.NewRequest("GET", "http://localhost"+route, nil))
		if rr.Code != 200 {
			t.Fatal(route, rr.Code)
		}
	}
	if _, err := os.Stat(filepath.Join(a.workspace, job.ID, "input.wav")); !os.IsNotExist(err) {
		t.Fatal("Upload not removed")
	}
}
func TestValidationAndBusy(t *testing.T) {
	release := make(chan struct{})
	a := testAPI(t, func(ctx context.Context, path string) error { <-release; return errors.New("private error") })
	for _, pair := range [][2]string{{"invalid", "song.wav"}, {"baseline", "song.exe"}} {
		w := upload(t, a, pair[0], pair[1])
		if w.Code != 400 {
			t.Fatal(w.Code)
		}
	}
	first := upload(t, a, "baseline", "song.wav")
	if first.Code != 202 {
		t.Fatal(first.Body.String())
	}
	second := upload(t, a, "baseline", "song.wav")
	if second.Code != 409 {
		t.Fatal(second.Code)
	}
	var j Job
	json.Unmarshal(first.Body.Bytes(), &j)
	rr := httptest.NewRecorder()
	a.handler().ServeHTTP(rr, httptest.NewRequest("GET", "http://localhost/api/jobs/"+j.ID+"/download", nil))
	if rr.Code != 409 {
		t.Fatal(rr.Code)
	}
	close(release)
	failed := waitJob(t, a, j.ID)
	if failed.Status != "failed" || strings.Contains(failed.Error, "private") {
		t.Fatal(failed)
	}
}
func TestUnknownJobAndOrigin(t *testing.T) {
	a := testAPI(t, nil)
	rr := httptest.NewRecorder()
	a.handler().ServeHTTP(rr, httptest.NewRequest("GET", "http://localhost/api/jobs/missing", nil))
	if rr.Code != 404 {
		t.Fatal(rr.Code)
	}
	req := httptest.NewRequest("POST", "http://localhost/api/jobs", nil)
	req.Header.Set("Origin", "https://untrusted.example")
	rr = httptest.NewRecorder()
	a.handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusForbidden {
		t.Fatal(rr.Code)
	}
}
func TestWorkerIntegration(t *testing.T) {
	if os.Getenv("MULTIVAC_INTEGRATION") != "1" {
		t.Skip("Set MULTIVAC_INTEGRATION=1 for actual Python worker test")
	}
	root, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(root, ".venv", "bin", "python")
	a := testAPI(t, nil)
	a.root = root
	a.run = func(ctx context.Context, path string) error { return runPython(ctx, python, root, path) }
	// Real WAV fixture, no API key and no copyrighted audio.
	wav := syntheticWAV()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	writer.WriteField("mode", "baseline")
	writer.WriteField("seconds", "2")
	writer.WriteField("model", "gpt-4o-mini")
	part, _ := writer.CreateFormFile("audio", "test.wav")
	io.Copy(part, bytes.NewReader(wav))
	writer.Close()
	req := httptest.NewRequest("POST", "http://localhost/api/jobs", &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	rr := httptest.NewRecorder()
	a.handler().ServeHTTP(rr, req)
	if rr.Code != 202 {
		t.Fatal(rr.Body.String())
	}
	var j Job
	json.Unmarshal(rr.Body.Bytes(), &j)
	deadline := time.Now().Add(60 * time.Second)
	for time.Now().Before(deadline) {
		job, _ := a.get(j.ID)
		if job.Status == "completed" {
			return
		}
		if job.Status == "failed" {
			t.Fatal(job)
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatal("Integration timeout")
}
