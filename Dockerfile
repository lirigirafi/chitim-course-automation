FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Copy your code
COPY . /var/task
WORKDIR /var/task

# Install dependencies
RUN pip install -r requirements.txt
RUN playwright install chromium

# Command to run your lambda handler
CMD [ "main.lambda_handler" ]
