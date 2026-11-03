"""Bounded local backend; put HTTPS/reverse proxy in front for a small beta."""
from http.server import ThreadingHTTPServer
import threading


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=64

    def __init__(self,*args,**kwargs):
        self.connections=threading.BoundedSemaphore(64)
        super().__init__(*args,**kwargs)

    def get_request(self):
        request,address=super().get_request();request.settimeout(20);return request,address

    def process_request(self,request,address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request);return
        try:super().process_request(request,address)
        except Exception:self.connections.release();raise

    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.connections.release()
