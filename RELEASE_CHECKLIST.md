# Release Checklist

## Secrets Configured

- [ ] `JWT_SECRET_KEY`
- [ ] `ANTHROPIC_API_KEY` if using Anthropic
- [ ] `OPENAI_API_KEY` if using OpenAI transcription
- [ ] `ASSEMBLYAI_API_KEY` if using AssemblyAI
- [ ] `RENDER_API_KEY` if using workflow-triggered deploys
- [ ] `RENDER_SERVICE_ID` if using workflow-triggered deploys

## Environment Variables

- [ ] `VIRALIX_DATA_DIR` configured for persistent storage
- [ ] `FFMPEG_PATH` configured if FFmpeg is not on the base image PATH
- [ ] `REACT_APP_API_URL` set correctly for the frontend build

## Deployment Checklist

- [ ] Backend service created on Render
- [ ] Persistent disk mounted at `/var/data/viralix`
- [ ] Frontend static site connected or alternate frontend host configured
- [ ] Auto deploy enabled for `main`
- [ ] Build context matches `backend/Dockerfile`
- [ ] Health check path set to `/health`
- [ ] Repository description and topics updated in GitHub

## Smoke Test Checklist

- [ ] Sign up and log in
- [ ] Upload a short MP4 with audio
- [ ] Confirm transcription completes
- [ ] Confirm clips are generated
- [ ] Confirm downloadable outputs work
- [ ] Confirm job history loads

## Post-Deployment Verification

- [ ] Open the deployed frontend
- [ ] Confirm backend health endpoint responds
- [ ] Run one real upload job in production
- [ ] Verify the persistent disk retains outputs after restart
- [ ] Check logs for FFmpeg, auth, or provider API errors
