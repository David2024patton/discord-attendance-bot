FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Install system fonts for Pillow image rendering
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py .
COPY dashboard.py .
COPY dinos.json .
COPY scrape_wiki.py .
COPY assets/ ./assets/

# Create state directory and declare as volume for persistence
RUN mkdir -p /app/data
VOLUME /app/data

# Expose dashboard port
EXPOSE 8080

# Run the bot
CMD ["python", "bot.py"]
