# Runner Supervisor

Agent local Windows para controlar os runners ReceitaBX, eCAC e eSocial.

Ele deve rodar na mesma maquina dos runners. O backend em Docker chama este agent via:

```text
RUNNER_CONTROL_AGENT_BASE_URL=http://host.docker.internal:5090
```

## Endpoints

- `GET /health`
- `GET /runners/control`
- `GET /runners/control/<runnerId>`
- `POST /runners/control/<runnerId>/start`
- `POST /runners/control/<runnerId>/restart`
- `POST /runners/control/<runnerId>/unlock`
- `POST /runners/control/<runnerId>/restart-and-unlock`

## Execucao

```powershell
pip install -r requirements.txt
.\run-supervisor.ps1
```

Copie `.env.example` para `.env` se precisar ajustar caminhos/porta.
