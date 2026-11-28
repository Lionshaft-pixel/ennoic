package com.ennoic.app;

/** Monotonic, per-task double-Back window. No history entries or credentials. */
final class RootBackState {
    static final long WINDOW_MILLIS = 2000L;
    private long deadline;

    boolean press(long now) {
        if (deadline > 0 && now < deadline) {
            reset();
            return true;
        }
        deadline = now + WINDOW_MILLIS;
        return false;
    }

    void reset() {
        deadline = 0;
    }
}
