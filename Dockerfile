# Shopping Agent — production Docker image
# Includes: Chrome, ChromeDriver, Xvfb, Python 3.11, all deps
#
# Build:  docker build -t shopping-agent .
# Run:    docker run -p 8000:8000 \
#           -e ANTHROPIC_API_KEY=sk-ant-... \
#           -e API_TOKEN=your-secret \
#           shopping-agent

FROM python:3.11-slim

# ---------- system deps + Chrome ----------
RUN apt-get update && apt-get install -y \
    wget \
    gnupg2 \
    ca-certificates \
    xvfb \
    xauth \
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    fonts-liberation \
    libappindicator3-1 \
    libgtk-3-0 \
    --no-install-recommends \
    && wget -q -O /usr/share/keyrings/google-chrome.gpg https://dl.google.com/linux/linux_signing_key.pub \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ---------- Python deps ----------
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- app code ----------
COPY . .

# ---------- runtime ----------
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99

EXPOSE 8000

# Start Xvfb (needed by pyvirtualdisplay) then launch the API
CMD Xvfb :99 -screen 0 1366x768x24 -ac +extension GLX +render -noreset & \
    uvicorn shopping_agent.api:app --host 0.0.0.0 --port ${PORT:-8000}
