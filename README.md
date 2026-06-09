# 🎬 Viralix

> AI-powered short-form video clipping platform that transforms long-form videos into engaging, captioned vertical clips for social media.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green)
![React](https://img.shields.io/badge/React-Frontend-61DAFB)
![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

# 📖 Overview

Viralix is an AI-assisted video processing platform designed to automate the creation of short-form content from long videos.

The application accepts uploaded videos or supported sources, extracts audio, performs transcription, identifies engaging moments, generates captions, and produces social-media-ready vertical clips.

The project demonstrates full-stack development, AI integration, authentication, Docker containerization, and automated deployment workflows.

---

# ✨ Features

* 🔐 JWT Authentication
* 🎥 Video Upload Pipeline
* 📝 Automatic Speech Transcription
* 🤖 AI-assisted Clip Selection
* ✂️ Automatic Video Clipping
* 💬 Caption Generation & Burn-in
* 📱 Vertical Video Output
* 📦 Docker Support
* 🚀 CI/CD with GitHub Actions
* ☁️ Render Deployment Blueprint
* 📊 Job Status Tracking
* 📥 Downloadable Processed Clips

---

# 🏗 Architecture

```text
                +------------------+
                | React Frontend   |
                +---------+--------+
                          |
                    REST API Calls
                          |
                +---------v--------+
                | FastAPI Backend  |
                +---------+--------+
                          |
          +---------------+----------------+
          |               |                |
     Authentication   Video Pipeline   Job Manager
          |               |                |
          |        FFmpeg Processing       |
          |               |                |
          |        AI Transcription        |
          |               |                |
          +---------------+----------------+
                          |
                    Generated Clips
                          |
                    Download Endpoint
```

---

# 🛠 Tech Stack

## Frontend

* React
* TypeScript
* React Router
* Axios
* Framer Motion
* Tailwind CSS

## Backend

* FastAPI
* SQLAlchemy
* Pydantic
* JWT Authentication
* Passlib
* Uvicorn

## AI & Media Processing

* FFmpeg
* Whisper / OpenAI Transcription
* Anthropic Integration
* yt-dlp

## DevOps

* Docker
* Docker Compose
* GitHub Actions
* Render Blueprint

---

# 🚀 Installation

## Clone

```bash
git clone https://github.com/udishgt/viralix.git
cd viralix
```

---

## Backend

```bash
cd backend

python -m venv .venv

source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

pip install -r requirements.txt

uvicorn server:app --reload
```

---

## Frontend

```bash
cd frontend

npm install

npm start
```

---

# 🐳 Docker

Build:

```bash
docker build -f backend/Dockerfile -t viralix .
```

Run:

```bash
docker compose up --build
```

---

# ⚙️ Environment Variables

Create a `.env` file using the following template:

```env
JWT_SECRET_KEY=your-secret-key

OPENAI_API_KEY=your-openai-key

ANTHROPIC_API_KEY=your-anthropic-key

ASSEMBLYAI_API_KEY=your-assemblyai-key

FFMPEG_PATH=/usr/bin/ffmpeg

REACT_APP_API_URL=http://localhost:8000
```

Never commit secrets to version control.

---

# 📡 API Overview

## Authentication

```
POST /auth/signup

POST /auth/login

POST /auth/logout

POST /auth/refresh
```

## Video Processing

```
POST /upload

GET /status/{job_id}

GET /jobs/{job_id}

GET /clips/{job_id}

GET /download/{job_id}/{filename}
```

---

# 📸 Screenshots

## Dashboard

*(Add screenshot here)*

---

## Upload Workflow

*(Add screenshot here)*

---

## Generated Clips

*(Add screenshot here)*

---

## Authentication

*(Add screenshot here)*

---

# ☁️ Deployment

The project includes:

* Docker support
* Docker Compose
* Render Blueprint (`render.yaml`)
* GitHub Actions CI/CD

Supported deployment targets:

* Render
* Google Cloud Run
* Railway
* Fly.io
* Self-hosted Docker

---

# 🧪 Local Development

Backend tests:

```bash
pytest
```

Frontend production build:

```bash
npm run build
```

Docker build:

```bash
docker build -f backend/Dockerfile .
```

---

# 🔮 Future Improvements

* PostgreSQL support
* Redis-backed job queue
* Background worker service
* S3-compatible object storage
* Multi-language transcription
* Team workspaces
* OAuth login
* Analytics dashboard
* Automatic social media publishing
* AI-powered title and hashtag generation

---

# 📁 Project Structure

```text
backend/
frontend/
uploads/
outputs/
.github/
Dockerfile
docker-compose.yml
render.yaml
README.md
```

---

# 🤝 Contributing

Contributions, suggestions, and bug reports are welcome.

Please open an issue or submit a pull request for improvements.

---

# 📄 License

This project is licensed under the MIT License.

---

# 👨‍💻 Author

**Udish Gupta**

* AI & Full-Stack Developer
* Passionate about Generative AI, Automation, and Scalable Web Applications

GitHub:
https://github.com/udishgt

LinkedIn:
(Add your LinkedIn profile here)

---

# ⭐ Portfolio Note

This project was built as a production-oriented AI video processing platform showcasing:

* Full-stack development
* AI integration
* Authentication systems
* Docker containerization
* CI/CD automation
* Cloud deployment
* Media processing pipelines

If you found this project interesting, consider giving it a ⭐ on GitHub.
