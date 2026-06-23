import time
import threading

class TokenBucket:
    '''
    TokenBucket Rate Limiter

    Each bucket starts full at 'max_tokens' and refils at the 'refill_rate' tokens every 
    second.
    '''

    def __init__(self, max_tokens:float, refill_rate: float):
        
        assert max_tokens>0, "max_tokens must be positive"
        assert refill_rate>0, "refill_rate must be positive"
    
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate

        self.tokens= max_tokens
        self.refilled_rate = time.time()
        self.lock = threading.Lock()

    def _refill(self):
        now = time.time()
        elapsed = now - self.refilled_rate
        if(elapsed>0):
            self.tokens = min(
                self.max_tokens, 
                self.tokens + elapsed * self.refill_rate
            )

            self.refilled_rate = now

    def allow_request(self, tokens:float=1) -> bool:
        with self.lock:
            self._refill()
            if self.tokens>=tokens:
                self.tokens -= tokens
                return True
            return False

    def get_remaining(self)-> float:
        with self.lock:
            self._refill()
            return self.tokens

    def get_reset_time(self) -> float:
        with self.lock:
            self._refill()
            return self.refilled_rate
        
                


        
