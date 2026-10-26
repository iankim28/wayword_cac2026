function createRecorder(hooks) {
  let recorder = null;
  let stream = null;
  let chunks = [];
  let generation = 0;

  async function finish(gen) {
    const mime = recorder ? recorder.mimeType : "";
    const blob = new Blob(chunks, { type: mime || "audio/webm" });
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
    recorder = null;
    if (gen !== generation) return;
    try {
      hooks.onReview(await toWav(blob));
    } catch (error) {
      hooks.onError("Could not read that recording.");
    }
  }

  return {
    cancel() {
      generation += 1;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      else if (stream) {
        stream.getTracks().forEach((track) => track.stop());
        stream = null;
      }
    },
    stop() {
      if (recorder && recorder.state !== "inactive") recorder.stop();
    },
    listen(blob) {
      const audio = new Audio(URL.createObjectURL(blob));
      audio.play();
    },
    async start() {
      const gen = generation + 1;
      generation = gen;
      chunks = [];
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        hooks.onError("Allow the microphone to record.");
        return;
      }
      if (gen !== generation) {
        stream.getTracks().forEach((track) => track.stop());
        stream = null;
        return;
      }
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((type) =>
        MediaRecorder.isTypeSupported(type)
      );
      recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      recorder.onstop = () => finish(gen);
      recorder.start();
      hooks.onRecording();
    },
  };
}

async function toWav(blob) {
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    return encodeWav(decoded.getChannelData(0), decoded.sampleRate);
  } finally {
    context.close();
  }
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function writeString(view, offset, text) {
  for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
}
