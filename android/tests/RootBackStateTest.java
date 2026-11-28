package com.ennoic.app;

/** Run with javac/java; intentionally independent of Android, Gradle and secrets. */
public final class RootBackStateTest {
    private static int checks;

    private static void check(boolean condition, String name) {
        checks++;
        if (!condition) throw new AssertionError(name);
    }

    public static void main(String[] args) {
        RootBackState state = new RootBackState();
        check(!state.press(0), "first press arms even at time zero");
        check(state.press(1999), "second press before deadline exits");
        check(!state.press(2000), "exit consumes the prior window");
        check(!state.press(4000), "exact two-second boundary resets");
        check(state.press(4001), "new window accepts a second press");
        check(!state.press(10000), "later first press arms");
        check(!state.press(12001), "expired window arms instead of exiting");
        state.reset();
        check(!state.press(12002), "navigation/reset cancels an armed window");
        check(state.press(12003), "fresh consecutive presses still exit");
        state.reset();
        state.reset();
        check(!state.press(20000), "repeated reset is safe");
        System.out.println("RootBackState: " + checks + " checks passed");
    }
}
