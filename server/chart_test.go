package main

import (
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestChartAndAudioServing(t *testing.T) {
	a := testAPI(t, nil)
	id := "0123456789abcdef0123456789abcdef"
	output := filepath.Join(a.workspace, id, "output")
	folder := filepath.Join(output, "Artist - Song")
	os.MkdirAll(folder, 0700)
	os.WriteFile(filepath.Join(folder, "chart_preview.json"), []byte(`{"notes":[]}`), 0600)
	os.WriteFile(filepath.Join(folder, "song.mp3"), []byte("audio-fixture"), 0600)
	os.WriteFile(filepath.Join(folder, "notes.mid"), []byte("midi-fixture"), 0600)
	os.WriteFile(filepath.Join(folder, "song.ini"), []byte("[song]\nname = Test"), 0600)
	a.jobs[id] = Job{ID: id, Status: "completed", Zip: filepath.Join(output, "song.zip")}
	for _, route := range []string{"chart", "audio", "midi", "ini"} {
		rr := httptest.NewRecorder()
		req := httptest.NewRequest("GET", "http://localhost/api/jobs/"+id+"/"+route, nil)
		if route == "audio" {
			req.Header.Set("Range", "bytes=0-4")
		}
		a.handler().ServeHTTP(rr, req)
		if route == "audio" {
			if rr.Code != 206 || rr.Body.String() != "audio" {
				t.Fatal(rr.Code, rr.Body.String())
			}
		} else if rr.Code != 200 {
			t.Fatal(rr.Code)
		}
	}
	a.jobs[id] = Job{ID: id, Status: "running"}
	rr := httptest.NewRecorder()
	a.handler().ServeHTTP(rr, httptest.NewRequest("GET", "http://localhost/api/jobs/"+id+"/chart", nil))
	if rr.Code != 409 {
		t.Fatal(rr.Code)
	}
}
