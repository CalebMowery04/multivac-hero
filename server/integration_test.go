package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"math"
	"os/exec"
	"path/filepath"
)

func runPython(ctx context.Context, python, root, path string) error {
	cmd := exec.CommandContext(ctx, python, filepath.Join(root, "worker.py"), path)
	cmd.Dir = root
	return cmd.Run()
}
func syntheticWAV() []byte {
	const sr = 22050
	pcm := make([]int16, sr*2)
	for i := range pcm {
		t := float64(i) / sr
		phase := math.Mod(t, .25)
		if phase < .08 {
			pcm[i] = int16(16000 * math.Sin(2*math.Pi*440*t) * math.Exp(-phase*40))
		}
	}
	var b bytes.Buffer
	b.WriteString("RIFF")
	binary.Write(&b, binary.LittleEndian, uint32(36+len(pcm)*2))
	b.WriteString("WAVEfmt ")
	for _, v := range []any{uint32(16), uint16(1), uint16(1), uint32(sr), uint32(sr * 2), uint16(2), uint16(16)} {
		binary.Write(&b, binary.LittleEndian, v)
	}
	b.WriteString("data")
	binary.Write(&b, binary.LittleEndian, uint32(len(pcm)*2))
	binary.Write(&b, binary.LittleEndian, pcm)
	return b.Bytes()
}
