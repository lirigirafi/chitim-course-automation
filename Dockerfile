FROM public.ecr.aws/lambda/python:3.12

# Install Playwright system dependencies
RUN dnf install -y atk cups-libs gtk3 libXcomposite alsa-lib \
    libXcursor libXdamage libXext libXi libXrandr libXScrnSaver \
    libXtst pango at-spi2-atk libXt xorg-x11-server-Xvfb \
    xorg-x11-xauth dbus-glib dbus-x11 nss mesa-libgbm && \
    dnf clean all

COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install chromium

COPY main.py email_monitor.py wordpress_automation.py config.py ./

CMD ["main.lambda_handler"]
