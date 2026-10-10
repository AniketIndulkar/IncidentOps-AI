# Postman collection

`IncidentOps-AI.postman_collection.json` holds every API request with built-in checks.
It grows with the app: each ticket that adds or changes an endpoint adds requests here.

## Use it in Postman

1. Postman → **Import** → select the collection file and both files in `environments/`.
2. Pick an environment (top right): **IncidentOps Local** or **IncidentOps AWS**.
3. For AWS, set `baseUrl` in that environment to `http://<server-ip>`.
4. Run one request, or the whole collection: collection → **Run** (order matters; requests share
   `incidentId` and `idempotencyKey`).

After editing in Postman, **Export** the collection (v2.1) over this file and commit it.

## Run it from the terminal (Newman)

```bash
npx newman run postman/IncidentOps-AI.postman_collection.json \
  -e postman/environments/local.postman_environment.json
```

Against a deployed server, without editing any file:

```bash
npx newman run postman/IncidentOps-AI.postman_collection.json --env-var baseUrl=http://$IP
```

## What it covers

| Folder | Requests |
|---|---|
| Health | `GET /health` |
| Incidents | create, retry with the same key (replay), same key with a different body (422), get, unknown id (404), malformed id (422), invalid body (422, no input echoed) |
