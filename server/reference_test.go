package main

import (
	"bytes"
	"context"
	"encoding/json"
	"mime/multipart"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReferenceApprovalGate(t *testing.T) {
	var calls int
	a := testAPI(t, func(ctx context.Context, path string) error {
		data, _ := os.ReadFile(path)
		var request Request
		json.Unmarshal(data, &request)
		calls++
		if request.Stage == "prepare" {
			data = []byte(`{"estimated_cost_usd":2.5,"reference_files":["guitar.mp3"],"reference_seconds":100}`)
			os.WriteFile(filepath.Join(filepath.Dir(path), "reference_mix.wav"), []byte("wav"), 0600)
		} else {
			os.MkdirAll(request.Output, 0700)
			zip := filepath.Join(request.Output, "song.zip")
			os.WriteFile(zip, []byte("zip"), 0600)
			data, _ = json.Marshal(map[string]any{"zip_path": zip, "mode": "llm"})
		}
		return os.WriteFile(request.Result, data, 0600)
	})
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	writer.WriteField("mode", "audio")
	writer.WriteField("seconds", "0")
	for _, file := range []struct{ field, name string }{{"audio", "target.mp3"}, {"reference", "notes.mid"}, {"reference", "guitar.mp3"}, {"reference", "preview.mp3"}, {"reference", "album.png"}} {
		part, _ := writer.CreateFormFile(file.field, file.name)
		part.Write([]byte("fixture"))
	}
	writer.Close()
	req := httptest.NewRequest("POST", "http://localhost/api/jobs", &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	rr := httptest.NewRecorder()
	a.handler().ServeHTTP(rr, req)
	if rr.Code != 202 {
		t.Fatal(rr.Code, rr.Body.String())
	}
	var job Job
	json.Unmarshal(rr.Body.Bytes(), &job)
	a.wg.Wait()
	prepared, _ := a.get(job.ID)
	if prepared.Status != "awaiting_approval" || calls != 1 {
		t.Fatal(prepared, calls)
	}
	for _, name := range []string{"preview.mp3", "album.png"} {
		if _, err := os.Stat(filepath.Join(a.root, "user_references", job.ID, name)); !os.IsNotExist(err) {
			t.Fatal("Ignored file stored", name)
		}
	}
	data, _ := os.ReadFile(filepath.Join(a.workspace, job.ID, "request.json"))
	if !strings.Contains(string(data), `"seconds":0`) {
		t.Fatal("Full target was clipped")
	}
	for _, body := range []string{`{"budget_usd":1}`, `{"budget_usd":0}`, `{}`} {
		denied := httptest.NewRecorder()
		a.handler().ServeHTTP(denied, httptest.NewRequest("POST", "http://localhost/api/jobs/"+job.ID+"/approve", strings.NewReader(body)))
		if denied.Code != 400 {
			t.Fatal("Invalid or insufficient budget accepted", denied.Code)
		}
	}
	if calls != 1 {
		t.Fatal("Generation ran without sufficient approval")
	}
	rr = httptest.NewRecorder()
	a.handler().ServeHTTP(rr, httptest.NewRequest("POST", "http://localhost/api/jobs/"+job.ID+"/approve", strings.NewReader(`{"budget_usd":3}`)))
	if rr.Code != 202 {
		t.Fatal(rr.Code, rr.Body.String())
	}
	a.wg.Wait()
	completed, _ := a.get(job.ID)
	if completed.Status != "completed" || calls != 2 {
		t.Fatal(completed, calls)
	}
	rr = httptest.NewRecorder()
	a.handler().ServeHTTP(rr, httptest.NewRequest("POST", "http://localhost/api/jobs/"+job.ID+"/approve", strings.NewReader(`{"budget_usd":3}`)))
	if rr.Code != 409 {
		t.Fatal("Duplicate approval accepted")
	}
}

func TestCancelPreparedJob(t *testing.T) {
	a := testAPI(t, nil)
	id := "0123456789abcdef0123456789abcdef"
	dir := filepath.Join(a.workspace, id)
	os.Mkdir(dir, 0700)
	os.WriteFile(filepath.Join(dir, "input.mp3"), []byte("fixture"), 0600)
	a.jobs[id] = Job{ID: id, Status: "awaiting_approval"}
	rr := httptest.NewRecorder()
	a.handler().ServeHTTP(rr, httptest.NewRequest("POST", "http://localhost/api/jobs/"+id+"/cancel", nil))
	if rr.Code != 200 {
		t.Fatal(rr.Code)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatal("Cancelled temporary files remain")
	}
	rr = httptest.NewRecorder()
	a.handler().ServeHTTP(rr, httptest.NewRequest("POST", "http://localhost/api/jobs/"+id+"/approve", strings.NewReader(`{"budget_usd":3}`)))
	if rr.Code != 409 {
		t.Fatal("Cancelled job approved")
	}
}

func TestPreparationErrorKeepsSizeBreakdown(t *testing.T) {
	a := testAPI(t, func(ctx context.Context, path string) error {
		data, _ := os.ReadFile(path)
		var request Request
		json.Unmarshal(data, &request)
		return os.WriteFile(request.Result, []byte(`{"preparation_error":"Too much context","size_breakdown":{"midi_text_tokens":123456}}`), 0600)
	})
	id := "0123456789abcdef0123456789abcdef"
	dir := filepath.Join(a.workspace, id)
	os.Mkdir(dir, 0700)
	request := Request{Stage: "prepare", Result: filepath.Join(dir, "estimate.json")}
	data, _ := json.Marshal(request)
	path := filepath.Join(dir, "request.json")
	os.WriteFile(path, data, 0600)
	a.slot <- struct{}{}
	a.wg.Add(1)
	a.process(id, dir, path, request.Result)
	job, _ := a.get(id)
	if job.Status != "failed" || job.Error != "Too much context" || job.Report["size_breakdown"] == nil {
		t.Fatal(job)
	}
}

func TestRecalculateInvalidatesOldEstimate(t *testing.T) {
	a := testAPI(t, func(ctx context.Context, path string) error {
		data, _ := os.ReadFile(path)
		var request Request
		json.Unmarshal(data, &request)
		if request.Stage != "prepare" || request.ReferenceSeconds != 10 || request.Seconds != 15 {
			t.Error("Wrong recalculation settings", request)
		}
		return os.WriteFile(request.Result, []byte(`{"estimated_cost_usd":0.25,"size_exceeded":false,"requested_reference_seconds":10,"requested_target_seconds":15}`), 0600)
	})
	id := "0123456789abcdef0123456789abcdef"
	dir := filepath.Join(a.workspace, id)
	os.Mkdir(dir, 0700)
	request := Request{Stage: "prepare", ReferenceSeconds: 300, Seconds: 300, Result: filepath.Join(dir, "estimate.json")}
	data, _ := json.Marshal(request)
	os.WriteFile(filepath.Join(dir, "request.json"), data, 0600)
	a.jobs[id] = Job{ID: id, Status: "awaiting_approval", Report: map[string]any{"estimated_cost_usd": 2.0, "size_exceeded": true}}
	denied := httptest.NewRecorder()
	a.handler().ServeHTTP(denied, httptest.NewRequest("POST", "http://localhost/api/jobs/"+id+"/approve", strings.NewReader(`{"budget_usd":100}`)))
	if denied.Code != 400 {
		t.Fatal("Oversized request approved")
	}
	rr := httptest.NewRecorder()
	a.handler().ServeHTTP(rr, httptest.NewRequest("POST", "http://localhost/api/jobs/"+id+"/recalculate", strings.NewReader(`{"reference_seconds":10,"seconds":15}`)))
	if rr.Code != 202 {
		t.Fatal(rr.Code, rr.Body.String())
	}
	a.wg.Wait()
	job, _ := a.get(id)
	if job.Status != "awaiting_approval" || job.Report["estimated_cost_usd"] != 0.25 {
		t.Fatal(job)
	}
	for _, body := range []string{`{"reference_seconds":-1,"seconds":15}`, `{"reference_seconds":10,"seconds":901}`} {
		rr = httptest.NewRecorder()
		a.handler().ServeHTTP(rr, httptest.NewRequest("POST", "http://localhost/api/jobs/"+id+"/recalculate", strings.NewReader(body)))
		if rr.Code != 400 {
			t.Fatal("Invalid duration accepted")
		}
	}
}
