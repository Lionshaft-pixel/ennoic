package com.ennoic.app;

import android.app.Activity;
import android.app.ActivityManager;
import android.content.ComponentName;
import android.content.Intent;
import android.graphics.Matrix;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.widget.ImageView;
import android.widget.Toast;

import androidx.browser.customtabs.CustomTabsCallback;
import androidx.browser.customtabs.CustomTabsClient;
import androidx.browser.customtabs.CustomTabsService;
import androidx.browser.customtabs.CustomTabsServiceConnection;
import androidx.browser.customtabs.CustomTabsSession;
import androidx.browser.trusted.TrustedWebActivityIntent;
import androidx.browser.trusted.TrustedWebActivityIntentBuilder;

import com.google.androidbrowserhelper.trusted.FocusActivity;
import com.google.androidbrowserhelper.trusted.LauncherActivityMetadata;
import com.google.androidbrowserhelper.trusted.QualityEnforcer;
import com.google.androidbrowserhelper.trusted.SessionStore;
import com.google.androidbrowserhelper.trusted.SharedPreferencesTokenStore;
import com.google.androidbrowserhelper.trusted.TwaProviderPicker;
import com.google.androidbrowserhelper.trusted.TwaLauncher;
import com.google.androidbrowserhelper.trusted.TwaSharedPreferencesManager;
import com.google.androidbrowserhelper.trusted.splashscreens.PwaWrapperSplashScreenStrategy;

import org.json.JSONException;
import org.json.JSONObject;

import java.security.SecureRandom;

/**
 * TWA host using the public Custom Tabs session APIs. The browser still renders
 * all pages and owns Back dispatch. A verified, origin-targeted MessagePort lets
 * the web CloseWatcher request only a native toast or closure of this own task.
 * No WebView, browser history manipulation, cookie access, or activity relaunch.
 */
public final class LauncherActivity extends Activity {
    private static final Uri ORIGIN = Uri.parse("https://ennoic.onrender.com");
    private static final String LAUNCHED_KEY = "ennoic.browserLaunched";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final RootBackState backState = new RootBackState();
    private final SecureRandom random = new SecureRandom();
    private final Runnable resetTimer = this::resetExit;
    private CustomTabsSession session;
    private CustomTabsServiceConnection connection;
    private PwaWrapperSplashScreenStrategy splash;
    private Toast exitToast;
    private String nonce;
    private boolean bound;
    private boolean validated;
    private boolean navigationFinished;
    private boolean browserLaunched;
    private boolean channelRequested;
    private int ownedTaskId;
    private int lastRequestId;
    private LauncherActivityMetadata metadata;

    private final CustomTabsCallback callback = new QualityEnforcer() {
        @Override
        public void onRelationshipValidationResult(int relation, Uri requestedOrigin,
                boolean result, Bundle extras) {
            if (relation != CustomTabsService.RELATION_USE_AS_ORIGIN
                    || !ORIGIN.equals(requestedOrigin)) return;
            validated = result;
            requestChannel();
        }

        @Override
        public void onNavigationEvent(int event, Bundle extras) {
            if (event == NAVIGATION_STARTED) {
                navigationFinished = false;
                channelRequested = false;
                nonce = null;
                resetExit();
            } else if (event == NAVIGATION_FINISHED) {
                navigationFinished = true;
                requestChannel();
            } else if (event == TAB_HIDDEN) {
                resetExit();
            } else if (event == TAB_SHOWN && browserLaunched && navigationFinished) {
                nonce = null;
                channelRequested = false;
                requestChannel();
            }
        }

        @Override
        public void onMessageChannelReady(Bundle extras) {
            if (!validated || !navigationFinished || session == null) return;
            // A VIEW intent can originate inside another application's task.
            // Never give such a tab permission to control its task's navigation.
            try {
                if (findOwnedTask() == null) return;
            } catch (SecurityException ignored) {
                return;
            }
            byte[] bytes = new byte[16];
            random.nextBytes(bytes);
            StringBuilder value = new StringBuilder();
            for (byte item : bytes) value.append(String.format("%02x", item & 0xff));
            nonce = value.toString();
            lastRequestId = 0;
            JSONObject hello = new JSONObject();
            try {
                hello.put("type", "ennoic.native").put("version", 1)
                        .put("capability", "back-v1").put("nonce", nonce);
                session.postMessage(hello.toString(), null);
            } catch (JSONException ignored) {
                nonce = null;
            }
        }

        @Override
        public void onPostMessage(String message, Bundle extras) {
            if (!validated || nonce == null || message == null || message.length() > 512) return;
            try {
                JSONObject request = new JSONObject(message);
                Object idValue = request.opt("requestId");
                if (!(idValue instanceof Integer)) return;
                int requestId = (Integer) idValue;
                if (!"ennoic.back".equals(request.optString("type"))
                        || !Integer.valueOf(1).equals(request.opt("version"))
                        || !nonce.equals(request.optString("nonce"))
                        || requestId < 1 || requestId <= lastRequestId) return;
                String action = request.optString("action");
                if (!"reset".equals(action) && !"root".equals(action) && !"exit".equals(action)) return;
                lastRequestId = requestId;
                if ("reset".equals(action)) {
                    resetExit();
                    reply(requestId, "reset");
                    return;
                }
                ActivityManager.AppTask task = findOwnedTask();
                if (task == null) {
                    resetExit();
                    reply(requestId, "unavailable");
                    return;
                }
                if ("exit".equals(action) || backState.press(SystemClock.elapsedRealtime())) {
                    resetExit();
                    reply(requestId, "exiting");
                    task.finishAndRemoveTask();
                    return;
                }
                handler.removeCallbacks(resetTimer);
                if (exitToast != null) exitToast.cancel();
                exitToast = Toast.makeText(getApplicationContext(),
                        "Press back again to exit", Toast.LENGTH_SHORT);
                exitToast.show();
                handler.postDelayed(resetTimer, RootBackState.WINDOW_MILLIS);
                reply(requestId, "armed");
            } catch (JSONException | SecurityException ignored) {
                resetExit();
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ownedTaskId = getTaskId();
        // A restored launcher must not reopen a TWA that the user already left.
        if (savedInstanceState != null && savedInstanceState.getBoolean(LAUNCHED_KEY)) {
            finish();
            return;
        }
        metadata = LauncherActivityMetadata.parse(this);
        TwaProviderPicker.Action provider = TwaProviderPicker.pickProvider(getPackageManager());
        Uri launchUrl = trustedLaunchUrl(getIntent());
        if (provider.launchMode != TwaProviderPicker.LaunchMode.TRUSTED_WEB_ACTIVITY) {
            launchFallback(launchUrl, provider.provider);
            return;
        }
        connection = new CustomTabsServiceConnection() {
            @Override
            public void onCustomTabsServiceConnected(ComponentName name, CustomTabsClient client) {
                if (isFinishing() || isDestroyed()) return;
                client.warmup(0);
                session = client.newSession(callback, SessionStore.makeSessionId(ownedTaskId));
                if (session == null) {
                    launchFallback(launchUrl, provider.provider);
                    return;
                }
                new SharedPreferencesTokenStore(LauncherActivity.this)
                        .setVerifiedProvider(provider.provider, getPackageManager());
                new TwaSharedPreferencesManager(LauncherActivity.this)
                        .writeLastLaunchedProviderPackageName(provider.provider);
                session.validateRelationship(CustomTabsService.RELATION_USE_AS_ORIGIN, ORIGIN, null);
                TrustedWebActivityIntentBuilder builder = new TrustedWebActivityIntentBuilder(launchUrl);
                metadata.configureIntentBuilder(builder, LauncherActivity.this);
                splash = new PwaWrapperSplashScreenStrategy(LauncherActivity.this,
                        metadata.splashImageDrawableId,
                        getColor(metadata.splashScreenBackgroundColorId),
                        ImageView.ScaleType.CENTER, new Matrix(),
                        metadata.splashScreenFadeOutDurationMillis,
                        metadata.fileProviderAuthority, metadata.startChromeBeforeAnimationComplete);
                splash.onTwaLaunchInitiated(provider.provider, builder);
                splash.configureTwaBuilder(builder, session, () -> {
                    if (isFinishing() || isDestroyed() || session == null) return;
                    TrustedWebActivityIntent intent = builder.build(session);
                    FocusActivity.addToIntent(intent.getIntent(), LauncherActivity.this);
                    browserLaunched = true;
                    intent.launchTrustedWebActivity(LauncherActivity.this);
                });
            }

            @Override
            public void onServiceDisconnected(ComponentName name) {
                session = null;
                nonce = null;
                validated = false;
                resetExit();
            }
        };
        bound = CustomTabsClient.bindCustomTabsService(this, provider.provider, connection);
        if (!bound) launchFallback(launchUrl, provider.provider);
    }

    private Uri trustedLaunchUrl(Intent intent) {
        Uri incoming = intent.getData();
        // Exported App Links are restricted to the existing verified HTTPS host.
        if (incoming != null && "https".equalsIgnoreCase(incoming.getScheme())
                && "ennoic.onrender.com".equalsIgnoreCase(incoming.getHost())
                && (incoming.getPort() == -1 || incoming.getPort() == 443)
                && incoming.getUserInfo() == null) return incoming;
        return Uri.parse(metadata.defaultUrl);
    }

    private void launchFallback(Uri url, String provider) {
        // Unsupported providers retain the existing Custom Tabs fallback. They
        // never receive a native-back capability or a JavaScript history trap.
        TrustedWebActivityIntentBuilder fallback = new TrustedWebActivityIntentBuilder(url);
        metadata.configureIntentBuilder(fallback, this);
        new SharedPreferencesTokenStore(this).store(null);
        new TwaSharedPreferencesManager(this).writeLastLaunchedProviderPackageName(null);
        TwaLauncher.CCT_FALLBACK_STRATEGY.launch(this, fallback, provider, this::finish);
    }

    private void requestChannel() {
        if (!validated || !navigationFinished || session == null || channelRequested) return;
        // The target origin prevents channel delivery to Google or checkout pages.
        channelRequested = session.requestPostMessageChannel(ORIGIN, ORIGIN, new Bundle());
    }

    private ActivityManager.AppTask findOwnedTask() {
        ActivityManager manager = (ActivityManager) getSystemService(ACTIVITY_SERVICE);
        if (manager == null) return null;
        for (ActivityManager.AppTask task : manager.getAppTasks()) {
            ActivityManager.RecentTaskInfo info = task.getTaskInfo();
            if (info == null || info.baseIntent == null) continue;
            int taskId = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ? info.taskId : info.id;
            if (taskId != ownedTaskId) continue;
            ComponentName owner = info.baseIntent.getComponent();
            if (owner != null && getPackageName().equals(owner.getPackageName())) return task;
        }
        return null;
    }

    private void reply(long requestId, String state) throws JSONException {
        if (session == null || nonce == null) return;
        session.postMessage(new JSONObject().put("type", "ennoic.native.back")
                .put("version", 1).put("nonce", nonce).put("requestId", requestId)
                .put("state", state).toString(), null);
    }

    private void resetExit() {
        backState.reset();
        handler.removeCallbacks(resetTimer);
        if (exitToast != null) {
            exitToast.cancel();
            exitToast = null;
        }
    }

    @Override
    public void onEnterAnimationComplete() {
        super.onEnterAnimationComplete();
        if (splash != null) splash.onActivityEnterAnimationComplete();
    }

    @Override
    protected void onRestart() {
        super.onRestart();
        // The browser has already closed. Do not reopen it on Back or relaunch.
        if (browserLaunched) finish();
    }

    @Override
    protected void onSaveInstanceState(Bundle state) {
        super.onSaveInstanceState(state);
        state.putBoolean(LAUNCHED_KEY, browserLaunched);
    }

    @Override
    protected void onDestroy() {
        resetExit();
        nonce = null;
        session = null;
        if (splash != null) splash.destroy();
        if (bound && connection != null) unbindService(connection);
        super.onDestroy();
    }
}
