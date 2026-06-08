# Deployment and CI for Viralix

This project includes CI workflows and containerization to help you publish Viralix similarly to your other repo.

What I added
- GitHub Actions CI: `.github/workflows/ci.yml` — runs backend tests, builds frontend, and builds/pushes a backend image to GHCR.
- Frontend deploy: `.github/workflows/deploy-frontend.yml` — builds `frontend` and publishes `frontend/build` to GitHub Pages.
- `backend/Dockerfile` — container image for the FastAPI backend.
- `docker-compose.yml` — easy local startup for the backend with mounted `uploads` and `outputs`.
- `render.yaml` — Render Blueprint for the backend Docker web service with a persistent disk.
- `.dockerignore` to keep images small.

Quick local run

1. Run backend with Docker Compose:

```bash
docker compose up --build
```

2. Build frontend locally (if you want to serve via GitHub Pages, skip serving locally):

```bash
cd frontend
npm ci
npm run build
```

GitHub setup (required)

- If you want the backend image pushed to GitHub Container Registry, enable the workflow secret `GITHUB_TOKEN` (already available) and ensure Actions permissions allow write to packages.
- To auto-deploy the frontend to GitHub Pages, enable GitHub Pages in repository settings (branch `gh-pages` created by the Action).
- For automatic deployment to Render or another host, create secrets `RENDER_SERVICE_ID` and `RENDER_API_KEY` and update the CI workflow to call the provider's API (I can add this for Render if you want).

- I added a Render deploy workflow: `.github/workflows/deploy-render.yml` — it builds/pushes the backend image to GitHub Container Registry and triggers a Render deploy via the Render API. Add the repository secrets `RENDER_API_KEY` and `RENDER_SERVICE_ID` (Settings → Secrets) for it to run.

Render deployment (recommended)

1. Create a new Render Blueprint service from this repository's root `render.yaml`.
2. Render will create two services:
	- `viralix-backend` as a Docker web service.
	- `viralix-frontend` as a static site with SPA rewrites.
3. Keep both services connected to the `main` branch with auto deploy enabled.
4. Accept the generated `JWT_SECRET_KEY` or replace it with your own secret in Render.
5. Confirm the persistent disk is mounted at `/var/data/viralix` so `uploads/`, `outputs/`, and `jobs.json` persist.
6. Provide any optional AI keys in the Render dashboard if you want remote transcription and moment scoring:
	- `ANTHROPIC_API_KEY`
	- `OPENAI_API_KEY`
	- `ASSEMBLYAI_API_KEY`
7. Render will inject `PORT`; the Dockerfile now listens on that port automatically.
8. The frontend static site uses `REACT_APP_API_URL=https://viralix-backend.onrender.com`, which matches the backend service name in the blueprint.

Important runtime environment variables used by the backend

- `VIRALIX_DATA_DIR` — base directory for uploads, outputs, and job metadata; Render mounts the persistent disk here.
- `JWT_SECRET_KEY` — required for token signing; Render can generate it automatically in `render.yaml`.
- `FFMPEG_PATH` — points to the FFmpeg binary in the container.
- `ANTHROPIC_API_KEY` — optional AI scoring and translation.
- `OPENAI_API_KEY` — optional transcription.
- `ASSEMBLYAI_API_KEY` — optional transcription.

Hosting notes — free tiers and longevity

- **Frontend (static):** GitHub Pages is free for public repositories and is effectively free forever for static sites. Use it for the `frontend/build` artifacts (workflow already set up).
- **Backend (server):** No major provider guarantees "free forever" for general server hosting. Providers with historically available free tiers include Render, Fly.io, Vercel/Netlify (for serverless/static), and Railway — these are useful for testing and low-traffic MVPs but can change. Treat backend free tiers as convenient for development; plan billing/alerts for production.

Recommendation: Use GitHub Pages for the frontend (free), and pick Render or Fly for an easy backend deploy; configure billing alerts before you scale to production.

Deployment notes for this repo

- Backend on Render: use [render.yaml](render.yaml) and the Dockerfile at [backend/Dockerfile](backend/Dockerfile).
- Frontend on Render: the same blueprint deploys [frontend](frontend) as a static site with BrowserRouter rewrites.
- Local Docker: use [docker-compose.yml](docker-compose.yml), which now mounts `./data` to `/var/data/viralix`.
- Existing GitHub Actions remain valid: the build context now matches the Dockerfile and Render Blueprint.

Next actions I can take for you

- Add a GitHub Actions job to push to Render (requires your API key). 
- Add infrastructure-as-code (Terraform) or a sample `render.yaml` for one-click deploy. 
- Run backend tests and frontend build locally in this environment before you push changes.

Which of the above should I do next? If you want full auto-deploy to Render or another provider, share which provider and I will scaffold the workflow (you'll need to add the provider secrets in repository settings).
