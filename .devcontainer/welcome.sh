#!/usr/bin/env bash
# Printed when a terminal attaches to the Codespace. Purely informational --
# it never fails the container start.
set +e

cat <<'BANNER'

  AI Security API -- development container ready.

  RUN THE APP
    python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
      then open the forwarded port 8000 (VS Code shows a popup, or use the
      PORTS tab). No API key needed -- it defaults to the offline classifier.

  RUN IT IN DOCKER
    docker compose up --build           # same image CI builds
    docker compose down                 # stop it
    docker images                       # what you have built
    docker ps                           # what is running
    docker exec -it <container> sh      # shell inside a running container

  RUN THE GATES
    pytest --cov=app                    # 117 tests
    python -m redteam.run_redteam       # 64 attacks vs 30 benign questions
    python -m evals.run_evals           # 38 intent classification cases
    python -m app.security.audit audit.log    # verify the audit hash chain

BANNER

docker --version 2>/dev/null || echo "  (docker is still starting -- give it a few seconds)"
echo
