# Use a lightweight python image
FROM python:3.11-slim

# Install system dependencies (needed for compilation/security libraries)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose port 5000 (default)
EXPOSE 5000

# Command to run the application
CMD ["python", "-m", "bot.main"]
