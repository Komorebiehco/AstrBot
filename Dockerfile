FROM node:22-bookworm-slim AS dashboard-build
WORKDIR /build/dashboard
COPY dashboard/package.json dashboard/pnpm-lock.yaml ./
RUN npm install --global pnpm@10.11.0 \
    && pnpm install --frozen-lockfile
COPY dashboard/ ./
COPY astrbot/__init__.py /build/astrbot/__init__.py
COPY astrbot/core/utils/t2i/template/shiki_runtime.iife.js /build/astrbot/core/utils/t2i/template/shiki_runtime.iife.js
RUN pnpm run build \
    && node -e "const fs=require('fs'); const v=fs.readFileSync('../astrbot/__init__.py','utf8').match(/__version__ = \"([^\"]+)\"/)[1]; fs.mkdirSync('dist/assets',{recursive:true}); fs.writeFileSync('dist/assets/version','v'+v);"

FROM python:3.12-slim
WORKDIR /AstrBot
ENV ASTRBOT_WEBUI_DIR=/AstrBot/astrbot/dashboard/dist

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    python3-dev \
    libffi-dev \
    libssl-dev \
    ca-certificates \
    bash \
    ffmpeg \
    libavcodec-extra \
    file \
    poppler-utils \
    p7zip-full \
    unzip \
    unrar-free \
    fonts-noto-cjk \
    curl \
    gnupg \
    git \
    ripgrep \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean

COPY . /AstrBot/
COPY --from=dashboard-build /build/dashboard/dist /AstrBot/astrbot/dashboard/dist

RUN python -m pip install uv \
    && echo "3.12" > .python-version \
    && uv lock \
    && uv export --format requirements.txt --output-file requirements.txt --frozen \
    && uv pip install -r requirements.txt --no-cache-dir --system \
    && uv pip install socksio uv pilk --no-cache-dir --system

# Preinstall dependencies restored with the presentation skill so startup can
# bind the web port without waiting for slow runtime package downloads.
RUN uv pip install playwright fonttools py7zr pytz --no-cache-dir --system

EXPOSE 6185

CMD ["python", "main.py"]
