"""Bounded automatic restarts for non-mutating native controller roles."""
import collections
import time


class RestartBudget:
    """Three restarts in five minutes; further faults pause the role."""
    def __init__(self, *, clock=time.monotonic, ledger=None, role=None):
        self.clock = clock
        self.attempts = collections.deque()
        self.ledger, self.role = ledger, role
        if (ledger is None) != (role is None):
            raise ValueError('ledger and role must be provided together')
        self.paused = ledger.restart_state(role)[0] if ledger else False

    def failed(self, *, retryable=True):
        if self.ledger:
            self.paused, delay = self.ledger.restart_state(self.role, failure=True, retryable=retryable)
            return delay
        if self.paused:
            return None
        if not retryable:
            self.paused = True
            return None
        now = self.clock()
        while self.attempts and now - self.attempts[0] >= 300:
            self.attempts.popleft()
        if len(self.attempts) >= 3:
            self.paused = True
            return None
        delay = (2, 10, 30)[len(self.attempts)]
        self.attempts.append(now)
        return delay
