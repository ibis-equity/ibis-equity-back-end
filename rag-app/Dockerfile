FROM python:3.11.2

WORKDIR python-docker

COPY requirements.txt requirements.txt

RUN pip3 install -r requirements.txt && mkdir /root/.streamlit

COPY . .

# Normalize Windows line endings and mark entrypoint script executable.
RUN sed -i 's/\r$//' run_app.sh && chmod +x run_app.sh

CMD [ "./run_app.sh" ]
