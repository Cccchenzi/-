"""Start the installed project-local embedding runtime; never download at launch."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request


def start_local_runtime(config):
    if config.get("RAG_PROVIDER") == "local":
        return None
    provider = config.get("EMBEDDING_PROVIDER") or config.get("RAG_PROVIDER")
    if provider != "ollama" or config.get("OLLAMA_BASE_URL", "").rstrip("/") != "http://127.0.0.1:11434":
        return None
    root = Path(__file__).resolve().parents[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def running():
        try:
            with opener.open("http://127.0.0.1:11434/api/tags", timeout=1) as response:
                return isinstance(json.load(response).get("models"), list)
        except Exception:
            return False

    if running():
        return None
    executable = root / ".runtime" / "ollama" / "ollama.exe"
    command = str(executable) if executable.exists() else shutil.which("ollama")
    if not command:
        print("Local embeddings are not installed. Run setup-ai.bat first.", flush=True)
        return None
    data_dir = Path(config["DATA_DIR"])
    environment = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434",
                       OLLAMA_MODELS=str(data_dir / "ollama-models"), OLLAMA_NO_CLOUD="1")
    proxy = urllib.request.getproxies().get("https")
    if proxy and not environment.get("HTTPS_PROXY"):
        environment["HTTPS_PROXY"] = proxy
    environment["NO_PROXY"] = "localhost,127.0.0.1,::1"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (data_dir / "ollama-stdout.log").open("ab") as output, (data_dir / "ollama-stderr.log").open("ab") as errors:
        process = subprocess.Popen([command, "serve"], env=environment, stdout=output, stderr=errors, creationflags=flags)
    for _ in range(45):
        if running():
            print("Local embedding service is ready.", flush=True)
            return process
        if process.poll() is not None:
            print("Embedding service failed to start. Check data/ollama-stderr.log.", flush=True)
            return None
        time.sleep(1)
    print("Embedding service is still starting; use the model check button after launch.", flush=True)
    return process
