FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY frontend ./frontend
COPY index.html vite.config.ts tsconfig.json ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY scripts ./scripts
COPY --from=frontend /app/dist ./dist
COPY datasets/geospatial ./reference
RUN useradd --create-home ocean && mkdir -p /app/datasets && chown -R ocean:ocean /app
USER ocean
ENV OCEAN_DATA=/app/datasets PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "scripts/container_start.py"]
