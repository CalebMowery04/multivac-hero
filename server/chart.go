package main

import (
	"net/http"
	"os"
	"path/filepath"
)

func (a *API) completedFolder(w http.ResponseWriter, r *http.Request) string {
	id := r.PathValue("id")
	job, ok := a.get(id)
	if !validID(id) || !ok {
		fail(w, 404, "Job not found")
		return ""
	}
	if job.Status != "completed" {
		fail(w, 409, "Chart is not ready")
		return ""
	}
	// Packages contain a single song folder alongside the result ZIP.
	root := filepath.Dir(job.Zip)
	entries, err := os.ReadDir(root)
	if err != nil {
		fail(w, 404, "Song files unavailable")
		return ""
	}
	for _, entry := range entries {
		if entry.IsDir() {
			return filepath.Join(root, entry.Name())
		}
	}
	fail(w, 404, "Song files unavailable")
	return ""
}
func (a *API) chart(w http.ResponseWriter, r *http.Request) {
	folder := a.completedFolder(w, r)
	if folder == "" {
		return
	}
	w.Header().Set("Content-Type", "application/json")
	http.ServeFile(w, r, filepath.Join(folder, "chart_preview.json"))
}
func (a *API) songAudio(w http.ResponseWriter, r *http.Request) {
	folder := a.completedFolder(w, r)
	if folder == "" {
		return
	}
	for _, ext := range []string{".mp3", ".wav", ".ogg", ".flac"} {
		path := filepath.Join(folder, "song"+ext)
		if info, err := os.Stat(path); err == nil && info.Mode().IsRegular() {
			w.Header().Set("X-Audio-Filename", "song"+ext)
			http.ServeFile(w, r, path)
			return
		}
	}
	fail(w, 404, "Audio unavailable")
}

func (a *API) midi(w http.ResponseWriter, r *http.Request) {
	folder := a.completedFolder(w, r)
	if folder == "" {
		return
	}
	w.Header().Set("Content-Type", "audio/midi")
	http.ServeFile(w, r, filepath.Join(folder, "notes.mid"))
}

func (a *API) songINI(w http.ResponseWriter, r *http.Request) {
	folder := a.completedFolder(w, r)
	if folder == "" {
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	http.ServeFile(w, r, filepath.Join(folder, "song.ini"))
}
