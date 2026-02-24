FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py .

# Create state directory
RUN mkdir -p /app/data

# Expose health check port
EXPOSE 8080

# Run the bot
CMD ["python", "bot.py"]
