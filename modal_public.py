import modal

app = modal.App("lotguard-sentinel-public-demo")

image = (
    modal.Image.from_registry(
        "falkordb/falkordb-server:latest",
        add_python="3.12",
    )
    .entrypoint([])
    .pip_install(
        "falkordb==1.8.0",
        "mcp==2.3.0",
    )
    .env({
        "PYTHONPATH": "/opt",
        "LOTGUARD_PUBLIC_DEMO": "1",
    })
    .add_local_dir(
        "src/lotguard",
        "/opt/lotguard",
    )
)


@app.function(image=image, cpu=1, memory=2048)
def check_environment():
    import os
    import sys
    import shutil

    from lotguard.sentinel_ui import PUBLIC_DEMO

    return {
        "python": sys.version.split()[0],
        "redis": shutil.which("redis-server"),
        "falkordb_module": os.path.isfile(
            "/var/lib/falkordb/bin/falkordb.so"
        ),
        "read_only_mode": PUBLIC_DEMO,
    }


@app.local_entrypoint()
def main():
    print(check_environment.remote())


@app.function(
    image=image,
    cpu=1,
    memory=2048,
    max_containers=1,
    scaledown_window=120,
)
@modal.web_server(8766, startup_timeout=120)
def serve_dashboard():
    import subprocess
    import sys
    import time

    from redis import Redis

    redis_process = subprocess.Popen([
        "redis-server",
        "--bind", "127.0.0.1",
        "--port", "6379",
        "--save", "",
        "--appendonly", "no",
        "--loadmodule",
        "/var/lib/falkordb/bin/falkordb.so",
    ])

    for _ in range(60):
        if redis_process.poll() is not None:
            raise RuntimeError("FalkorDB stopped during startup.")

        try:
            Redis(host="127.0.0.1", port=6379).ping()
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("FalkorDB startup timed out.")

    subprocess.run(
        [sys.executable, "-m", "lotguard.deep_lineage"],
        check=True,
    )

    subprocess.Popen([
        sys.executable,
        "-m",
        "lotguard.sentinel_ui",
    ])
