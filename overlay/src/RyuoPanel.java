import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorMatrix;
import android.graphics.ColorMatrixColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.PorterDuff;
import android.graphics.PorterDuffColorFilter;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.graphics.fonts.Font;
import android.graphics.fonts.FontFamily;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Looper;
import android.text.Layout;
import android.text.StaticLayout;
import android.text.TextPaint;
import android.view.Surface;
import android.view.SurfaceControl;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;

/**
 * 龙屏额度面板的屏上常驻程序：在分屏右边那条 634×1080 上方开一个图层，用原生画布实时绘制。
 *
 * 由电脑端 tools/native_panel.py 拉起：
 *   CLASSPATH=/data/local/tmp/ryuo/panel.dex app_process /data/local/tmp/ryuo RyuoPanel
 *
 * 电脑经 adb forward 连到抽象套接字 ryuo-panel，一行一条 JSON：
 *   {"t":"state","theme":{...},"data":{...},"mode":{...}}   换主题、数据或显示方式
 *     mode 不给就是分屏窄条；{"full":true,"show":15,"hide":45} 是整屏：视频放 45 秒，
 *     面板淡入盖在视频上 15 秒（视频在底下压暗继续播），如此循环
 *   {"t":"ping"}                               保活；20 秒收不到任何消息就自己藏起来
 *   {"t":"hide"} / {"t":"quit"}
 * 倒计时、时间进度、过期提示都在屏上按秒自己走，电脑只需隔一会儿送一次新数据。
 */
public final class RyuoPanel {
    static final String DIR = "/data/local/tmp/ryuo";
    static final String SOCKET = "ryuo-panel";
    static final int H = 1080, FULL_W = 2240;
    // 窄条宽度由电脑送来；玻璃模式下底下是整屏视频，面板半透明叠在上面
    static volatile int W = 634, X = 1606;
    static volatile boolean glassMode;
    static final long SILENT_HIDE_MS = 20_000, STALE_AFTER_MS = 30 * 60_000, ANIM_MS = 900;

    static volatile Theme theme;
    static volatile Data data;
    static volatile long lastMsg;
    static volatile boolean hideReq, quitReq;
    static volatile long dataVersion;
    static volatile boolean full;
    static volatile long showMs = 15_000, hideMs = 45_000;
    static final long FADE_MS = 700;

    public static void main(String[] args) throws Exception {
        Looper.prepareMainLooper();
        // 不经 zygote 启动时系统字体没预载，先手动载入，否则一画字就崩
        Typeface.class.getMethod("loadPreinstalledSystemFontMap").invoke(null);
        LocalServerSocket server;
        try {
            server = new LocalServerSocket(SOCKET);
        } catch (java.io.IOException e) {
            System.out.println("已经有一个在跑了，退出");
            return;
        }
        Thread net = new Thread(() -> serve(server), "net");
        net.setDaemon(true);
        net.start();
        new RyuoPanel().loop();
    }

    // ---------------- 收消息 ----------------

    static void serve(LocalServerSocket server) {
        while (!quitReq) {
            try (LocalSocket s = server.accept()) {
                BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
                OutputStream out = s.getOutputStream();
                String line;
                while ((line = in.readLine()) != null) {
                    String reply = "ok";
                    try {
                        handle(new JSONObject(line));
                    } catch (Exception e) {
                        reply = "err " + e;
                        System.out.println("消息出错：" + e);
                    }
                    out.write((reply + "\n").getBytes(StandardCharsets.UTF_8));
                    out.flush();
                    if (quitReq) return;
                }
            } catch (Exception e) {
                System.out.println("连接断开：" + e);
            }
        }
    }

    static void handle(JSONObject m) throws Exception {
        lastMsg = System.currentTimeMillis();
        switch (m.getString("t")) {
            case "state":
                if (m.has("theme")) theme = Theme.load(m.getJSONObject("theme"));
                if (m.has("data")) {
                    data = Data.parse(m.getJSONObject("data"));
                    dataVersion++;
                }
                if (m.has("mode")) {
                    JSONObject md = m.getJSONObject("mode");
                    full = md.optBoolean("full", false);
                    showMs = md.optLong("show", 15) * 1000;
                    hideMs = md.optLong("hide", 45) * 1000;
                    int w = Math.max(560, Math.min(1120, md.optInt("width", 634)));
                    W = w;
                    X = FULL_W - w;
                    glassMode = md.optBoolean("glass", false);
                }
                hideReq = false;
                break;
            case "hide":
                hideReq = true;
                break;
            case "quit":
                quitReq = true;
                break;
            default: // ping
        }
    }

    // ---------------- 画 ----------------

    final Map<String, Float> shown = new HashMap<>();
    Map<String, Float> from = new HashMap<>();
    long animStart, seenVersion = -1;

    SurfaceControl sc;
    Surface surface;
    boolean layerFull, visible;
    int layerW;
    float alpha = -1;
    final SurfaceControl.Transaction t = new SurfaceControl.Transaction();

    /** 分屏只盖右边窄条，整屏盖满；切换时换一个尺寸合适的新图层。 */
    void layer(boolean wantFull) throws Exception {
        if (sc != null && layerFull == wantFull && (wantFull || layerW == W)) return;
        if (sc != null) {
            t.reparent(sc, null).apply();
            surface.release();
            sc.release();
        }
        sc = new SurfaceControl.Builder().setName("ryuo-panel").setBufferSize(wantFull ? FULL_W : W, H)
                .setFormat(PixelFormat.TRANSLUCENT).build();
        SurfaceControl.Transaction.class.getMethod("setLayerStack", SurfaceControl.class, int.class).invoke(t, sc, 0);
        t.setLayer(sc, Integer.MAX_VALUE).setPosition(sc, wantFull ? 0 : X, 0).setVisibility(sc, false).apply();
        surface = new Surface(sc);
        layerFull = wantFull;
        layerW = W;
        visible = false;
        alpha = -1;
    }

    /** 整屏循环里此刻面板该有多不透明：淡入、停留、淡出、藏起。 */
    static float cycleAlpha(long now) {
        long period = showMs + hideMs, ph = now % period;
        if (ph >= showMs) return 0;
        return Math.max(0, Math.min(1, Math.min(ph, showMs - ph) / (float) FADE_MS));
    }

    void loop() throws Exception {
        System.out.println("就绪，等电脑送数据");
        while (!quitReq) {
            long now = System.currentTimeMillis();
            Theme th = theme;
            Data d = data;
            boolean isFull = full;
            layer(isFull);
            float a = isFull ? cycleAlpha(now) : 1;
            boolean want = th != null && d != null && !hideReq && now - lastMsg < SILENT_HIDE_MS && a > 0;
            if (want != visible) {
                t.setVisibility(sc, want).apply();
                visible = want;
                if (want) seenVersion = -1; // 每次出现都从零转上来，看得出是活的
                System.out.println(want ? "显示" : "隐藏");
            }
            boolean animating = false;
            if (want) {
                if (a != alpha) {
                    t.setAlpha(sc, a).apply();
                    alpha = a;
                }
                animating = step(d, now) || a < 1;
                Canvas c = surface.lockHardwareCanvas();
                try {
                    new Painter(c, th, d, shown, now, isFull).draw();
                } finally {
                    surface.unlockCanvasAndPost(c);
                }
            }
            long sleep = animating ? 16 : 1000 - System.currentTimeMillis() % 1000;
            if (isFull && !animating) { // 别睡过下一次淡入
                long period = showMs + hideMs, ph = now % period;
                long untilShow = ph >= showMs ? period - ph : showMs - FADE_MS - ph;
                if (untilShow > 0) sleep = Math.min(sleep, untilShow);
            }
            Thread.sleep(Math.max(5, sleep));
        }
        if (sc != null) {
            t.reparent(sc, null).apply();
            surface.release();
            sc.release();
        }
        System.out.println("退出");
    }

    /** 数字和圆环从旧值缓动到新值；返回是否还在动画中。 */
    boolean step(Data d, long now) {
        if (seenVersion != dataVersion) {
            seenVersion = dataVersion;
            from = new HashMap<>(shown);
            animStart = now;
        }
        float k = Math.min(1f, (now - animStart) / (float) ANIM_MS);
        float e = 1 - (float) Math.pow(1 - k, 3);
        for (Row r : d.rows) {
            float a = from.containsKey(r.key) ? from.get(r.key) : 0f;
            shown.put(r.key, a + (r.used - a) * e);
        }
        return k < 1;
    }

    // ---------------- 主题 ----------------

    static final class Theme {
        String id, layout;
        int ink, dim, faint, line, accent, accent2, hot, mark, warn, logoInk;
        Typeface head, num, name, text, mono;
        Bitmap strip;
        float scrim;
        String title, next;
        boolean wideTitle;
        int[] series;
        final Map<String, Bitmap> logos = new HashMap<>();
        final Map<String, Boolean> logoMono = new HashMap<>();
        static final Map<String, Typeface> FONT_CACHE = new HashMap<>();

        static Theme load(JSONObject j) throws Exception {
            Theme th = new Theme();
            th.id = j.getString("id");
            th.layout = j.getString("layout");
            JSONObject c = j.getJSONObject("colors");
            th.ink = col(c, "ink");
            th.dim = col(c, "dim");
            th.faint = col(c, "faint");
            th.line = col(c, "line");
            th.accent = col(c, "accent");
            th.accent2 = col(c, "accent2");
            th.hot = col(c, "hot");
            th.mark = col(c, "mark");
            th.warn = col(c, "warn");
            th.logoInk = col(c, "logoInk");
            JSONObject f = j.getJSONObject("fonts");
            th.head = font(f.optJSONObject("head"));
            th.num = font(f.optJSONObject("num"));
            th.name = font(f.optJSONObject("name"));
            th.text = font(f.optJSONObject("text"));
            th.mono = font(f.optJSONObject("mono"));
            File strip = new File(DIR, j.getString("strip"));
            th.strip = strip.exists() ? BitmapFactory.decodeFile(strip.getPath()) : null;
            th.scrim = (float) j.optDouble("scrim", .74);
            JSONObject lb = j.optJSONObject("labels");
            boolean tubes = "tubes".equals(th.layout);
            th.title = lb != null ? lb.getString("title") : tubes ? "额度" : "AI 额度";
            th.next = lb != null ? lb.getString("next") : tubes ? "下 一 更" : "接下来";
            th.wideTitle = tubes && lb == null; // 墨夜流金的「额 度」是故意拉开的
            JSONArray se = j.optJSONArray("series");
            th.series = new int[4];
            for (int i = 0; i < 4; i++) th.series[i] = se != null && se.length() > 0 ? Color.parseColor(se.getString(i % se.length())) : th.accent;
            JSONObject lg = j.optJSONObject("logos");
            if (lg != null) {
                for (java.util.Iterator<String> it = lg.keys(); it.hasNext(); ) {
                    String k = it.next();
                    JSONObject o = lg.getJSONObject(k);
                    File p = new File(DIR, o.getString("file"));
                    if (!p.exists()) continue; // 开源版不带图标，缺了就只显示文字
                    th.logos.put(k, BitmapFactory.decodeFile(p.getPath()));
                    th.logoMono.put(k, o.optBoolean("mono", true));
                }
            }
            return th;
        }

        static int col(JSONObject c, String k) throws Exception {
            return Color.parseColor(c.getString(k));
        }

        /** {"file":"fonts/x.ttf","ttc":2,"weight":500}；空就用系统无衬线（带中文）。 */
        static Typeface font(JSONObject o) throws Exception {
            if (o == null) return Typeface.create("sans-serif", Typeface.NORMAL);
            String key = o.toString();
            Typeface cached = FONT_CACHE.get(key);
            if (cached != null) return cached;
            Font.Builder b = new Font.Builder(new File(DIR, o.getString("file")));
            if (o.has("ttc")) b.setTtcIndex(o.getInt("ttc"));
            if (o.has("weight")) b.setWeight(o.getInt("weight"));
            Typeface tf = new Typeface.CustomFallbackBuilder(new FontFamily.Builder(b.build()).build())
                    .setSystemFallback(o.optString("fallback", "sans-serif")).build();
            FONT_CACHE.put(key, tf);
            return tf;
        }
    }

    // ---------------- 数据 ----------------

    static final class Row {
        String key, name, sub, pace, tone, secondLabel;
        float used;
        int secondUsed = -1;
        long startMs, resetMs;
    }

    static final class Data {
        long generatedAtMs;
        TimeZone tz;
        final List<Row> rows = new ArrayList<>();

        static Data parse(JSONObject j) throws Exception {
            Data d = new Data();
            d.generatedAtMs = j.getLong("generatedAtMs");
            d.tz = TimeZone.getTimeZone(j.optString("tz", "UTC"));
            JSONArray a = j.getJSONArray("rows");
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                if (o.optBoolean("missing")) continue;
                Row r = new Row();
                r.key = o.getString("key");
                r.name = o.getString("name");
                r.sub = o.getString("sub");
                r.pace = o.getString("pace");
                r.tone = o.getString("tone");
                r.used = (float) o.getDouble("used");
                r.startMs = o.getLong("startMs");
                r.resetMs = o.getLong("resetMs");
                JSONObject s = o.optJSONObject("second");
                if (s != null) {
                    r.secondLabel = s.getString("label");
                    r.secondUsed = s.getInt("used");
                }
                d.rows.add(r);
            }
            return d;
        }
    }

    // ---------------- 画法（照 render/panel.html 的窄条） ----------------

    static final class Painter {
        static final String WEEKDAY = "日一二三四五六";
        final Canvas c;
        final Theme th;
        final Data d;
        final Map<String, Float> shown;
        final long now;
        final boolean stale;
        final List<Row> soon;
        final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);

        final boolean full;

        Painter(Canvas c, Theme th, Data d, Map<String, Float> shown, long now, boolean full) {
            this.full = full;
            this.c = c;
            this.th = th;
            this.d = d;
            this.shown = shown;
            this.now = now;
            this.stale = now - d.generatedAtMs > STALE_AFTER_MS;
            soon = new ArrayList<>(d.rows);
            soon.sort((a, b) -> Long.compare(a.resetMs, b.resetMs));
        }

        void draw() {
            c.drawColor(0, PorterDuff.Mode.CLEAR);
            if (full) {
                drawFull();
                return;
            }
            if (glassMode) {
                backdrop(0);
            } else if (th.strip != null) {
                c.drawBitmap(th.strip, null, new Rect(0, 0, W, H), new Paint(Paint.FILTER_BITMAP_FLAG));
            } else {
                c.drawColor(0xFF05080D);
            }
            if (d.rows.isEmpty()) return;
            // 防烧屏：内容每 10 分钟挪一个位置，在 ±3 像素里绕圈
            int[] dd = drift();
            c.save();
            c.translate(dd[0], dd[1]);
            switch (th.layout) {
                case "tubes": tubes(); break;
                case "cards": cards(); break;
                case "orbits": orbits(); break;
                default: rings();
            }
            c.restore();
        }

        int[] drift() {
            int phase = (int) (now / 600_000 % 8);
            return new int[]{new int[]{0, 2, 3, 2, 0, -2, -3, -2}[phase], new int[]{-3, -2, 0, 2, 3, 2, 0, -2}[phase]};
        }

        /** 整屏：左边 1606 压暗底下的视频，画四家大仪表；右边 634 放窄条背景和「接下来」。 */
        void drawFull() {
            p.reset();
            float sc = glassMode ? Math.min(th.scrim, .5f) : th.scrim;
            p.setColor(Color.argb(Math.round(sc * 255), 0, 0, 0));
            c.drawRect(0, 0, X, H, p);
            if (glassMode) {
                c.save();
                c.translate(X, 0);
                backdrop(sc);
                c.restore();
            } else if (th.strip != null) c.drawBitmap(th.strip, null, new Rect(X, 0, FULL_W, H), new Paint(Paint.FILTER_BITMAP_FLAG));
            if (d.rows.isEmpty()) return;
            int[] dd = drift();
            c.save();
            c.translate(dd[0], dd[1]);
            big();
            c.translate(X, 0);
            side();
            c.restore();
        }

        void big() {
            float x0 = 84, x1 = X - 84, top = 72, bottom = H - 66;
            boolean tubes = "tubes".equals(th.layout);
            float y;
            if (tubes) y = header(th.title, x0, x1, top, th.head, 72 * 50 / 56f, th.accent, th.wideTitle ? .32f : .06f, 30, true);
            else if ("cards".equals(th.layout)) y = header(th.title, x0, x1, top, th.head, 72 * 48 / 56f, th.ink, .01f, 30, true);
            else y = header(th.title, x0, x1, top, th.head, 72, th.ink, .04f, 30, true);
            String legend = "inkgold".equals(th.id) ? "金色是已经用掉的，红线是这一期的时间走到哪了"
                    : "orbits".equals(th.layout) ? "每一道光环是一家，亮的是已经用掉的，白点是这一期的时间走到哪了"
                    : "彩色是已经用掉的，白色细线是这一期的时间走到哪了";
            float legTop = bottom - 30;
            text(legend, x0, legTop, 30, font(th.text, 24, th.dim, 0), 0);
            float rowTop = y + 30, rowBottom = legTop - 24;
            if ("orbits".equals(th.layout)) {
                bigOrbits(x0, x1, rowTop, rowBottom);
                return;
            }
            int n = Math.min(4, d.rows.size());
            float colW = (x1 - x0 - 30 * 3) / 4;
            for (int i = 0; i < n; i++) {
                Row r = d.rows.get(i);
                float cx = x0 + i * (colW + 30) + colW / 2;
                if (tubes) bigTube(r, cx, colW, rowTop, rowBottom);
                else if ("cards".equals(th.layout)) bigCard(r, cx, colW, rowTop + 60, rowBottom - 40);
                else bigRing(r, cx, rowTop, rowBottom);
            }
        }

        /** 仪表下面三行字：名字、小字、几号重置。返回下沿。 */
        float bigLabels(Row r, float cx, float top, String sub) {
            text(r.name, cx, top + 8, 60, font(th.name, 48, th.ink, 0), 1);
            text(sub, cx, top + 76, 34, font(th.text, 27, hot(r) ? th.hot : th.dim, 0), 1);
            text(resetAt(r) + " 重置", cx, top + 118, 30, font(th.mono, 24, alpha(th.ink, .55f), 0), 1);
            return top + 148;
        }

        void bigRing(Row r, float cx, float rowTop, float rowBottom) {
            float h = 340 + 148, top = (rowTop + rowBottom - h) / 2;
            int s = gaugeBegin(new RectF(cx - 180, top - 10, cx + 180, top + 350));
            ring(r, cx, top, 340, 52);
            gaugeEnd(s);
            bigLabels(r, cx, top + 340, subText(r));
        }

        void bigTube(Row r, float cx, float colW, float rowTop, float rowBottom) {
            float tubeH = 360, h = 56 + 16 + 112 + 26 + tubeH + 148, top = (rowTop + rowBottom - h) / 2;
            int s = gaugeBegin(new RectF(cx - colW / 2, top, cx + colW / 2, top + h - 148));
            logo(r.key, cx, top + 28, 56);
            numWithUnit(usedText(r), "%", cx, top + 56 + 16, 112, font(th.num, 118, th.ink, 0), 40, 2, 1);
            float tt = top + 56 + 16 + 112 + 26;
            tube(r, cx, tt, tubeH, 14, 54);
            gaugeEnd(s);
            bigLabels(r, cx, tt + tubeH, r.sub + " · " + r.pace);
        }

        void bigCard(Row r, float cx, float colW, float top, float bottom) {
            RectF card = new RectF(cx - colW / 2, top, cx + colW / 2, bottom);
            int s = gaugeBegin(card);
            glass(card, 44, .07f, .02f, .09f);
            float content = 84 + 128 + 18 + 148 + 20 * 3, y = card.centerY() - content / 2;
            RectF chip = new RectF(cx - 42, y, cx + 42, y + 84);
            chip(chip, 24);
            logo(r.key, cx, chip.centerY(), 46);
            y += 84 + 20;
            numWithUnit(usedText(r), "%", cx, y, 128, font(th.num, 150, th.ink, -.04f), 46, 2, 1);
            y += 128 + 20;
            capsule(r, new RectF(card.left + 24, y, card.right - 24, y + 18));
            y += 18 + 20 - 8;
            gaugeEnd(s);
            bigLabels(r, cx, y, subText(r));
        }

        void side() {
            boolean tubes = "tubes".equals(th.layout);
            float x0 = tubes ? 84 : 46, x1 = W - 46, y = 72;
            text(th.next, x0, y, 62, font(th.head, 50, th.ink, .08f), 0);
            Row nx = soon.get(0);
            y += 62 + 40;
            float lx = x0;
            if (th.logos.containsKey(nx.key)) {
                logo(nx.key, x0 + 32, y + 32, 64);
                lx += 64 + 18;
            }
            text(nx.name, lx, y, 64, font(th.name, 44, th.ink, 0), 0);
            y += 64 + 18;
            String[] in = resetIn(nx);
            TextPaint np = font(th.num, 120, th.ink, -.03f);
            Paint.FontMetrics fm = np.getFontMetrics();
            float base = y + (120 - (fm.descent - fm.ascent)) / 2 - fm.ascent;
            c.drawText(in[0], x0, base, np);
            c.drawText(in[1] + "后重置", x0 + np.measureText(in[0]) + 10, base, font(th.text, 30, th.dim, 0));
            y += 120 + 10;
            text("重置前还剩 " + Math.round(100 - nx.used) + "%", x0, y, 34, font(th.text, 27, th.dim, 0), 0);
            y += 34 + 46;
            p.reset();
            for (Row r : soon) {
                p.setColor(th.line);
                c.drawRect(x0, y, x1, y + 1, p);
                float mid = y + 1 + 20 + 19;
                float tx = x0;
                if (th.logos.containsKey(r.key)) {
                    logo(r.key, x0 + 18, mid, 36);
                    tx += 36 + 16;
                }
                text(r.name, tx, mid - 19, 38, font(th.text, 30, th.ink, 0), 0);
                text(resetAt(r), x1, mid - 19, 38, font(th.mono, 26, th.dim, 0), 2);
                y += 1 + 20 + 38 + 20;
            }
            String stamp = stale ? "● 数据停在 " + hm(d.generatedAtMs) : "实时更新";
            text(stamp, x0, H - 66 - 31, 31, font(th.text, 25, stale ? th.warn : th.dim, 0), 0);
        }

        // ---- 公共小件 ----

        float elapsed(Row r) {
            float e = (now - r.startMs) / (float) (r.resetMs - r.startMs) * 100;
            return Math.max(0, Math.min(100, e));
        }

        float used(Row r) {
            Float v = shown.get(r.key);
            return v == null ? r.used : v;
        }

        String usedText(Row r) {
            return String.valueOf(Math.round(used(r)));
        }

        boolean hot(Row r) {
            return "hot".equals(r.tone);
        }

        String subText(Row r) {
            return r.secondLabel != null ? r.sub + " · " + r.secondLabel + " " + r.secondUsed + "%" : r.sub + " · " + r.pace;
        }

        /** 「6 天」「3 小时」「12 分钟」：数字和单位分开，单位后面接「后重置」。 */
        String[] resetIn(Row r) {
            long ms = r.resetMs - now;
            double h = ms / 3_600_000.0;
            if (h < 1) return new String[]{String.valueOf(Math.max(1, Math.round(h * 60))), "分钟"};
            if (h < 48) return new String[]{String.valueOf(Math.round(h)), "小时"};
            return new String[]{String.valueOf((long) (h / 24)), "天"};
        }

        String resetAt(Row r) {
            Calendar t = Calendar.getInstance(d.tz), n = Calendar.getInstance(d.tz);
            t.setTimeInMillis(r.resetMs);
            n.setTimeInMillis(now);
            String hm = String.format("%02d:%02d", t.get(Calendar.HOUR_OF_DAY), t.get(Calendar.MINUTE));
            Calendar tomorrow = (Calendar) n.clone();
            tomorrow.add(Calendar.DAY_OF_YEAR, 1);
            if (sameDay(t, n)) return "今天 " + hm;
            if (sameDay(t, tomorrow)) return "明天 " + hm;
            return (t.get(Calendar.MONTH) + 1) + "/" + t.get(Calendar.DAY_OF_MONTH) + " 周" + WEEKDAY.charAt(t.get(Calendar.DAY_OF_WEEK) - 1) + " " + hm;
        }

        static boolean sameDay(Calendar a, Calendar b) {
            return a.get(Calendar.YEAR) == b.get(Calendar.YEAR) && a.get(Calendar.DAY_OF_YEAR) == b.get(Calendar.DAY_OF_YEAR);
        }

        String md(long ms) {
            Calendar t = Calendar.getInstance(d.tz);
            t.setTimeInMillis(ms);
            return String.format("%02d-%02d", t.get(Calendar.MONTH) + 1, t.get(Calendar.DAY_OF_MONTH));
        }

        String hm(long ms) {
            Calendar t = Calendar.getInstance(d.tz);
            t.setTimeInMillis(ms);
            return String.format("%02d:%02d", t.get(Calendar.HOUR_OF_DAY), t.get(Calendar.MINUTE));
        }

        static String paceLine(Row r) {
            switch (r.pace) {
                case "偏快": return "用得偏快，悠着点";
                case "正好": return "节奏正好";
                default: return "还有富余，放心用";
            }
        }

        static int alpha(int color, float a) {
            return (color & 0x00FFFFFF) | (Math.round(Color.alpha(color) * a) << 24);
        }

        TextPaint font(Typeface tf, float size, int color, float spacingEm) {
            TextPaint tp = new TextPaint(Paint.ANTI_ALIAS_FLAG);
            tp.setSubpixelText(true);
            tp.setTypeface(tf);
            tp.setTextSize(size);
            tp.setColor(color);
            tp.setLetterSpacing(spacingEm);
            tp.setFontFeatureSettings("'tnum','lnum'");
            if (glassMode) tp.setShadowLayer(10, 0, 2, 0x8C000000); // 视频亮的地方字也看得清
            return tp;
        }

        /** 按 CSS 行盒放字：top 是行盒顶，lh 是行高，返回字宽。align：0 左，1 居中，2 右对齐到 x。 */
        float text(String s, float x, float top, float lh, Paint paint, int align) {
            Paint.FontMetrics fm = paint.getFontMetrics();
            float base = top + (lh - (fm.descent - fm.ascent)) / 2 - fm.ascent;
            float w = paint.measureText(s);
            float left = align == 1 ? x - w / 2 : align == 2 ? x - w : x;
            c.drawText(s, left, base, paint);
            return w;
        }

        void logo(String key, float cx, float cy, float size) {
            Bitmap b = th.logos.get(key);
            if (b == null) return;
            Paint lp = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
            if (Boolean.TRUE.equals(th.logoMono.get(key))) lp.setColorFilter(new PorterDuffColorFilter(th.logoInk, PorterDuff.Mode.SRC_IN));
            c.drawBitmap(b, null, new RectF(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2), lp);
        }

        /** 数据过期时仪表变灰变淡。 */
        int gaugeBegin(RectF area) {
            if (!stale) return -1;
            Paint gp = new Paint();
            ColorMatrix m = new ColorMatrix();
            m.setSaturation(0);
            gp.setColorFilter(new ColorMatrixColorFilter(m));
            gp.setAlpha(102);
            return c.saveLayer(area, gp);
        }

        void gaugeEnd(int save) {
            if (save >= 0) c.restoreToCount(save);
        }

        /** 头部：大标题 + 右边「● 13:48 更新」，过期时换成橙色提示条。返回下沿。 */
        float header(String title, float x0, float x1, float top, Typeface tf, float size, int color, float spacing) {
            return header(title, x0, x1, top, tf, size, color, spacing, 24, false);
        }

        float header(String title, float x0, float x1, float top, Typeface tf, float size, int color, float spacing, float tsSize, boolean withDate) {
            text(title, x0, top, size, font(tf, size, color, spacing), 0);
            float bottom = top + size;
            if (!stale) {
                TextPaint ts = font(th.mono, tsSize, th.dim, .02f);
                String s = (withDate ? md(d.generatedAtMs) + " " : "") + hm(d.generatedAtMs) + " 更新";
                float tsLh = tsSize * 1.25f;
                float w = text(s, x1, bottom - tsLh, tsLh, ts, 2);
                p.reset();
                p.setAntiAlias(true);
                p.setColor(th.accent);
                p.setShadowLayer(12, 0, 0, th.accent);
                c.drawCircle(x1 - w - 10 - 4, bottom - tsLh / 2, 4, p);
                p.clearShadowLayer();
                return bottom;
            }
            String msg = "数据停在 " + hm(d.generatedAtMs) + "，已经 " + (now - d.generatedAtMs) / 60_000 + " 分钟没更新";
            TextPaint bp = font(th.text, 26, th.warn, 0);
            int innerW = (int) (x1 - x0 - 36 - 24);
            StaticLayout sl = StaticLayout.Builder.obtain(msg, 0, msg.length(), bp, innerW)
                    .setAlignment(Layout.Alignment.ALIGN_NORMAL).setLineSpacing(0, 1.12f).build();
            float bt = bottom + 20, bh = sl.getHeight() + 24;
            RectF box = new RectF(x0, bt, x1, bt + bh);
            p.reset();
            p.setAntiAlias(true);
            p.setColor(alpha(th.warn, .10f));
            c.drawRoundRect(box, 12, 12, p);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(1);
            p.setColor(alpha(th.warn, .38f));
            c.drawRoundRect(box, 12, 12, p);
            p.setStyle(Paint.Style.FILL);
            p.setColor(th.warn);
            c.drawCircle(x0 + 18 + 6, bt + 12 + sl.getLineBottom(0) / 2f, 6, p);
            c.save();
            c.translate(x0 + 18 + 24, bt + 12);
            sl.draw(c);
            c.restore();
            return bt + bh;
        }

        /** 「接下来」块的内容（不含外框）：返回内容高度。dry=true 时只量不画。 */
        float nextBody(String label, Typeface kFont, float kSize, float kSpacing, float x0, float x1, float top, boolean dry) {
            Row nx = soon.get(0);
            float y = top, kLh = kSize * 1.25f;
            if (!dry) text(label, x0, y, kLh, font(kFont, kSize, kColor, kSpacing), 0);
            y += kLh + 14;
            if (!dry) {
                float mid = y + 45 / 2f;
                float lx = x0;
                if (th.logos.containsKey(nx.key)) {
                    logo(nx.key, x0 + 19, mid, 38);
                    lx += 38 + 16;
                }
                text(nx.name, lx, y, 45, font(th.name, 36, th.ink, 0), 0);
                String[] in = resetIn(nx);
                TextPaint unit = font(th.text, 22, th.dim, 0);
                float uw = unit.measureText(in[1] + "后重置");
                TextPaint np = font(th.num, 46, th.ink, 0);
                Paint.FontMetrics fm = np.getFontMetrics();
                float base = mid + (-fm.ascent - fm.descent) / 2;
                c.drawText(in[1] + "后重置", x1 - uw, base, unit);
                c.drawText(in[0], x1 - uw - 6 - np.measureText(in[0]), base, np);
            }
            y += 45 + 8;
            if (!dry) text("重置前还剩 " + Math.round(100 - nx.used) + "% · " + paceLine(nx), x0, y, 32, font(th.text, 23, th.dim, 0), 0);
            y += 32 + 16;
            for (int i = 1; i < soon.size(); i++) {
                Row r = soon.get(i);
                if (!dry) {
                    float lx = x0;
                    if (th.logos.containsKey(r.key)) {
                        logo(r.key, x0 + 13, y + 14.5f, 26);
                        lx += 26 + 12;
                    }
                    text(r.name, lx, y, 29, font(th.text, 23, th.dim, 0), 0);
                    text(resetAt(r), x1, y, 29, font(th.mono, 23, alpha(th.ink, .7f), 0), 2);
                }
                y += 29 + (i < soon.size() - 1 ? 10 : 0);
            }
            return y - top;
        }

        int kColor;

        // ---- 星舰舱：环形表 ----

        void rings() {
            float x0 = 42, x1 = W - 42;
            float y = header(th.title, x0, x1, 46, th.head, 56, th.ink, .04f);
            // 标题下的细线：一小段亮色 + 一截暗色
            p.reset();
            p.setColor(th.accent);
            c.drawRect(x0, y + 24, x0 + 44, y + 26, p);
            p.setColor(alpha(th.accent, .30f));
            c.drawRect(x0 + 56, y + 24, x1, y + 26, p);
            float gridTop = y + 26 + 20;
            float colW = (x1 - x0 - 6) / 2;
            for (int i = 0; i < d.rows.size() && i < 4; i++) {
                Row r = d.rows.get(i);
                float cx = x0 + (i % 2) * (colW + 6) + colW / 2;
                float top = gridTop + (i / 2) * (281 + 4);
                int s = gaugeBegin(new RectF(cx - 112, top - 5, cx + 112, top + 219));
                ring(r, cx, top, 214, 30);
                gaugeEnd(s);
                text(r.name, cx, top + 210, 40, font(th.name, 32, th.ink, .02f), 1);
                text(subText(r), cx, top + 254, 27, font(th.text, 21, hot(r) ? th.hot : th.dim, 0), 1);
            }
            // 接下来：带角标的框，贴底
            kColor = th.accent;
            float ix0 = x0 + 22, ix1 = x1 - 22;
            float bodyH = nextBody(th.next, th.head, 28, .12f, ix0, ix1, 0, true);
            float bh = 18 + bodyH + 16, bt = H - 42 - bh;
            RectF box = new RectF(x0, bt, x1, bt + bh);
            p.reset();
            p.setShader(new LinearGradient(0, bt, 0, bt + bh, alpha(th.accent, .07f), 0, Shader.TileMode.CLAMP));
            c.drawRect(box, p);
            p.setShader(null);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(1);
            p.setColor(alpha(th.accent, .22f));
            c.drawRect(box.left + .5f, box.top + .5f, box.right - .5f, box.bottom - .5f, p);
            p.setStyle(Paint.Style.FILL);
            p.setColor(th.accent);
            float l = box.left - 2, tt = box.top - 2, rr = box.right + 2, bb = box.bottom + 2;
            c.drawRect(l, tt, l + 18, tt + 2, p);
            c.drawRect(l, tt, l + 2, tt + 18, p);
            c.drawRect(rr - 18, bb - 2, rr, bb, p);
            c.drawRect(rr - 2, bb - 18, rr, bb, p);
            nextBody(th.next, th.head, 28, .12f, ix0, ix1, bt + 18, false);
        }

        void ring(Row r, float cx, float top, float size, float logoPx) {
            float cy = top + size / 2, R = size * .40f, sw = size * .048f;
            p.reset();
            p.setAntiAlias(true);
            p.setStrokeWidth(2);
            for (int i = 0; i < 28; i++) {
                double a = Math.toRadians(135 + i * 10);
                float r1 = R + sw * .95f, r2 = r1 + (i % 9 == 0 ? 14 : 6);
                p.setColor(alpha(th.accent, i % 9 == 0 ? .75f : .25f));
                c.drawLine(cx + r1 * (float) Math.cos(a), cy + r1 * (float) Math.sin(a), cx + r2 * (float) Math.cos(a), cy + r2 * (float) Math.sin(a), p);
            }
            RectF oval = new RectF(cx - R, cy - R, cx + R, cy + R);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeCap(Paint.Cap.ROUND);
            p.setStrokeWidth(sw);
            p.setColor(th.line);
            c.drawArc(oval, 135, 270, false, p);
            float u = used(r);
            float sweep = Math.max(.4f, 270 * u / 100);
            boolean hot = hot(r);
            p.setShader(new LinearGradient(cx - R, cy + R, cx + R, cy - R, hot ? 0xFFFF7A45 : th.accent2, hot ? th.hot : th.accent, Shader.TileMode.CLAMP));
            p.setMaskFilter(new BlurMaskFilter(size * .02f * 2, BlurMaskFilter.Blur.NORMAL));
            p.setAlpha(140);
            c.drawArc(oval, 135, sweep, false, p);
            p.setMaskFilter(null);
            p.setAlpha(255);
            c.drawArc(oval, 135, sweep, false, p);
            p.setShader(null);
            // 白色刻线：这一期的时间走到哪了
            double ea = Math.toRadians(135 + 270 * elapsed(r) / 100);
            float m1 = R - sw * 1.2f, m2 = R + sw * 1.2f;
            p.setStrokeWidth(4);
            p.setColor(th.mark);
            c.drawLine(cx + m1 * (float) Math.cos(ea), cy + m1 * (float) Math.sin(ea), cx + m2 * (float) Math.cos(ea), cy + m2 * (float) Math.sin(ea), p);
            // 中间：图标 + 百分数
            float numSize = size * .3f, numLh = numSize * .9f;
            boolean hasLogo = th.logos.containsKey(r.key);
            float blockH = (hasLogo ? logoPx + 4 : 0) + numLh;
            float y = cy - blockH / 2;
            if (hasLogo) {
                logo(r.key, cx, y + logoPx / 2, logoPx);
                y += logoPx + 4;
            }
            numWithUnit(usedText(r), "%", cx, y, numLh, font(th.num, numSize, th.ink, -.03f), numSize * .36f, 3, 1);
        }

        /** 大数字后面跟一个小单位，比如 29%。align 同 text()。 */
        void numWithUnit(String n, String unit, float x, float top, float lh, TextPaint np, float unitSize, float gap, int align) {
            float nw = np.measureText(n);
            Paint.FontMetrics fm = np.getFontMetrics();
            float base = top + (lh - (fm.descent - fm.ascent)) / 2 - fm.ascent;
            Typeface tf = np.getTypeface();
            float numSize = np.getTextSize();
            int color = np.getColor();
            Paint up = new Paint(Paint.ANTI_ALIAS_FLAG);
            up.setTypeface(tf);
            up.setTextSize(unitSize);
            up.setColor(th.dim);
            float uw = up.measureText(unit);
            float total = nw + gap + uw;
            float left = align == 1 ? x - total / 2 : align == 2 ? x - total : x;
            Paint num = new Paint(np);
            num.setColor(color);
            num.setTextSize(numSize);
            c.drawText(n, left, base, num);
            c.drawText(unit, left + nw + gap, base, up);
        }

        // ---- 墨夜流金：漏刻 ----

        void tubes() {
            float x0 = 76, x1 = W - 36;
            float y = header(th.title, x0, x1, 46, th.head, 50, th.accent, th.wideTitle ? .32f : .06f);
            kColor = th.accent;
            float bodyH = nextBody(th.next, th.head, 26, th.wideTitle ? .3f : .12f, x0, x1, 0, true);
            float nt = H - 42 - bodyH - 22 - 1;
            p.reset();
            p.setColor(th.line);
            c.drawRect(x0, nt, x1, nt + 1, p);
            nextBody(th.next, th.head, 26, th.wideTitle ? .3f : .12f, x0, x1, nt + 1 + 22, false);
            float colsTop = y + 40, colsBottom = nt - 30;
            float fixed = 40 + 16 + 70 + 22 + 20 + 40 + 4 + 27;
            float tubeH = Math.min(460, colsBottom - colsTop - fixed);
            float colW = (x1 - x0) / 4;
            for (int i = 0; i < d.rows.size() && i < 4; i++) {
                Row r = d.rows.get(i);
                float cx = x0 + colW * (i + .5f);
                float top = colsTop;
                int s = gaugeBegin(new RectF(cx - colW / 2, top, cx + colW / 2, top + 40 + 16 + 70 + 22 + tubeH + 2));
                logo(r.key, cx, top + 20, 40);
                numWithUnit(usedText(r), "%", cx, top + 40 + 16, 70, font(th.num, 74, th.ink, 0), 28, 1, 1);
                float tt = top + 40 + 16 + 70 + 22, tb = tt + tubeH;
                tube(r, cx, tt, tubeH, 10, 36);
                gaugeEnd(s);
                text(r.name, cx, tb + 20, 40, font(th.name, 32, th.ink, .04f), 1);
                text(r.sub, cx, tb + 64, 27, font(th.text, 21, hot(r) ? th.hot : alpha(th.accent, .85f), 0), 1);
            }
        }

        // ---- 日冕：同心光环 ----

        void orbits() {
            float x0 = 42, x1 = W - 42;
            float y = header(th.title, x0, x1, 46, th.head, 56, th.ink, .04f);
            float size = 500, top = y + 22;
            int s = gaugeBegin(new RectF(W / 2f - size / 2 - 10, top - 10, W / 2f + size / 2 + 10, top + size + 10));
            orb(W / 2f, top + size / 2, size);
            gaugeEnd(s);
            float ly = top + size + 24;
            for (int i = 0; i < d.rows.size() && i < 4; i++) {
                Row r = d.rows.get(i);
                p.reset();
                p.setColor(th.line);
                c.drawRect(x0, ly, x1, ly + 1, p);
                legendRow(r, i, x0, x1, ly + 11, 60, 6, 46, 28, 28, 20, 46, 20, 14);
                ly += 81;
            }
            Row nx = soon.get(0);
            text(nx.name + " 重置前还剩 " + Math.round(100 - nx.used) + "% · " + paceLine(nx), x0, H - 42 - 31, 31, font(th.text, 22, th.dim, 0), 0);
        }

        void bigOrbits(float x0, float x1, float rowTop, float rowBottom) {
            float size = 680, mid = (rowTop + rowBottom) / 2;
            int s = gaugeBegin(new RectF(x0 - 10, mid - size / 2 - 10, x0 + size + 10, mid + size / 2 + 10));
            orb(x0 + size / 2, mid, size);
            gaugeEnd(s);
            float lx0 = x0 + size + 60;
            int n = Math.min(4, d.rows.size());
            float rowH = 132, ly = mid - rowH * n / 2;
            for (int i = 0; i < n; i++) {
                p.reset();
                p.setColor(th.line);
                c.drawRect(lx0, ly, x1, ly + 1, p);
                legendRow(d.rows.get(i), i, lx0, x1, ly + 23, 87, 8, 80, 52, 44, 26, 92, 34, 22);
                ly += rowH;
            }
        }

        /** 图例一行：色条、图标、名字和重置时间、右边百分数。top/h 是这一行内容区。 */
        void legendRow(Row r, int i, float x0, float x1, float top, float h, float dotW, float dotH, float logoPx,
                       float nameSize, float subSize, float pcSize, float pcUnit, float gap) {
            float mid = top + h / 2;
            p.reset();
            p.setAntiAlias(true);
            p.setColor(th.series[i % 4]);
            c.drawRoundRect(new RectF(x0, mid - dotH / 2, x0 + dotW, mid + dotH / 2), dotW / 2, dotW / 2, p);
            float x = x0 + dotW + gap;
            if (th.logos.containsKey(r.key)) {
                logo(r.key, x + logoPx / 2, mid, logoPx);
                x += logoPx + gap;
            }
            float nLh = nameSize * 1.2f, sLh = subSize * 1.3f, bt = mid - (nLh + sLh) / 2;
            text(r.name, x, bt, nLh, font(th.name, nameSize, th.ink, 0), 0);
            text(resetAt(r) + " 重置", x, bt + nLh, sLh, font(th.text, subSize, hot(r) ? th.hot : th.dim, 0), 0);
            numWithUnit(usedText(r), "%", x1, mid - pcSize / 2, pcSize, font(th.num, pcSize, th.ink, -.02f), pcUnit, 2, 2);
        }

        /** 四道同心光环，外圈是第一家；中间写最近一次重置的倒计时。 */
        void orb(float cx, float cy, float size) {
            float sw = size * .05f, gap = size * .08f, R0 = size / 2 - sw;
            for (int i = 0; i < d.rows.size() && i < 4; i++) {
                Row r = d.rows.get(i);
                float R = R0 - i * gap;
                int col = th.series[i % 4];
                RectF oval = new RectF(cx - R, cy - R, cx + R, cy + R);
                p.reset();
                p.setAntiAlias(true);
                p.setStyle(Paint.Style.STROKE);
                p.setStrokeWidth(sw);
                p.setColor(alpha(col, .13f));
                c.drawCircle(cx, cy, R, p);
                float sweep = Math.max(.4f, 360 * used(r) / 100);
                p.setStrokeCap(Paint.Cap.ROUND);
                p.setColor(alpha(col, .6f));
                p.setMaskFilter(new BlurMaskFilter(size * .012f * 2, BlurMaskFilter.Blur.NORMAL));
                c.drawArc(oval, -90, sweep, false, p);
                p.setMaskFilter(null);
                p.setColor(col);
                c.drawArc(oval, -90, sweep, false, p);
                double ea = Math.toRadians(-90 + 360 * elapsed(r) / 100);
                p.setStyle(Paint.Style.FILL);
                p.setColor(th.mark);
                c.drawCircle(cx + R * (float) Math.cos(ea), cy + R * (float) Math.sin(ea), sw * .2f, p);
            }
            Row nx = soon.get(0);
            String[] in = resetIn(nx);
            float k = size / 500;
            float nLh = 76 * k, uLh = 26 * k, wLh = 28 * k, total = nLh + 6 * k + uLh + 6 * k + wLh;
            float y = cy - total / 2;
            text(in[0], cx, y, nLh, font(th.num, 84 * k, th.ink, -.03f), 1);
            y += nLh + 6 * k;
            text(in[1] + "后重置", cx, y, uLh, font(th.text, 21 * k, th.dim, 0), 1);
            y += uLh + 6 * k;
            TextPaint wp = font(th.name, 22 * k, th.ink, 0);
            float lp = th.logos.containsKey(nx.key) ? size * .045f : 0;
            float ww = lp + (lp > 0 ? 8 * k : 0) + wp.measureText(nx.name), wx = cx - ww / 2;
            if (lp > 0) logo(nx.key, wx + lp / 2, y + wLh / 2, lp);
            text(nx.name, wx + ww, y, wLh, wp, 2);
        }

        // ---- 液态光谱：玻璃卡 ----

        void cards() {
            float x0 = 42, x1 = W - 42;
            float y = header(th.title, x0, x1, 46, th.head, 48, th.ink, .01f);
            kColor = th.dim;
            float bodyH = nextBody(th.next, th.text, 22, .2f, x0 + 24, x1 - 24, 0, true);
            float bh = 18 + bodyH + 18, bt = H - 42 - bh;
            glass(new RectF(x0, bt, x1, bt + bh), 28, .04f, .04f, .07f);
            nextBody(th.next, th.text, 22, .2f, x0 + 24, x1 - 24, bt + 18, false);
            float top = y + 28, bottom = bt - 16;
            int n = Math.min(4, d.rows.size());
            float ch = (bottom - top - 14 * (n - 1)) / n;
            for (int i = 0; i < n; i++) {
                Row r = d.rows.get(i);
                RectF card = new RectF(x0, top + i * (ch + 14), x1, top + i * (ch + 14) + ch);
                int s = gaugeBegin(card);
                glass(card, 28, .07f, .02f, .09f);
                float cx0 = card.left + 24, cx1 = card.right - 24;
                float rowH = 73, contentTop = card.centerY() - (rowH + 14 + 14) / 2;
                float mid = contentTop + rowH / 2;
                // 图标小方块
                RectF chip = new RectF(cx0, mid - 29, cx0 + 58, mid + 29);
                chip(chip, 17);
                logo(r.key, chip.centerX(), chip.centerY(), 32);
                float wx = cx0 + 58 + 16;
                text(r.name, wx, mid - 31.5f, 35, font(th.name, 32, th.ink, 0), 0);
                text(subText(r), wx, mid + 5.5f, 26, font(th.text, 21, hot(r) ? th.hot : th.dim, 0), 0);
                numWithUnit(usedText(r), "%", cx1, contentTop, rowH, font(th.num, 86, th.ink, -.04f), 30, 2, 2);
                // 胶囊条
                float capTop = contentTop + rowH + 14;
                capsule(r, new RectF(cx0, capTop, cx1, capTop + 14));
                gaugeEnd(s);
            }
        }

        /** 用量渐变：平时从主题的第二色到主色，用得太快时换成暖色。 */
        int[] fill(Row r) {
            if (!hot(r)) return new int[]{th.accent2, th.accent};
            int h = th.hot;
            return new int[]{Color.rgb(Color.red(h) * 55 / 100, Color.green(h) * 55 / 100, Color.blue(h) * 55 / 100), h};
        }

        /** 竖着的漏刻：底色细管 + 从下往上的用量 + 一道时间刻线。 */
        void tube(Row r, float cx, float tt, float tubeH, float w, float markW) {
            float tb = tt + tubeH, rad = Math.min(5, w / 2);
            RectF tube = new RectF(cx - w / 2, tt, cx + w / 2, tb);
            p.reset();
            p.setAntiAlias(true);
            p.setColor(alpha(th.accent, .10f));
            c.drawRoundRect(tube, rad, rad, p);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(1);
            p.setColor(alpha(th.accent, .28f));
            c.drawRoundRect(new RectF(tube.left + .5f, tube.top + .5f, tube.right - .5f, tube.bottom - .5f), rad - .5f, rad - .5f, p);
            p.setStyle(Paint.Style.FILL);
            float fh = tubeH * used(r) / 100;
            if (fh > .5f) {
                int[] g = fill(r);
                p.setColor(0xFF000000); // 渐变会乘上画笔的透明度，先还原成不透明
                p.setShader(new LinearGradient(0, tb, 0, tb - fh, g[0], g[1], Shader.TileMode.CLAMP));
                c.drawRoundRect(new RectF(tube.left, tb - fh, tube.right, tb), rad, rad, p);
                p.setShader(null);
            }
            float my = tb - tubeH * elapsed(r) / 100 - 3;
            p.setColor(th.mark);
            c.drawRoundRect(new RectF(cx - markW / 2, my, cx + markW / 2, my + 3), 2, 2, p);
        }

        /** 横着的胶囊条。 */
        void capsule(Row r, RectF cap) {
            float rad = cap.height() / 2;
            p.reset();
            p.setAntiAlias(true);
            p.setColor(0x12FFFFFF);
            c.drawRoundRect(cap, rad, rad, p);
            float fw = cap.width() * Math.max(2, used(r)) / 100;
            int[] g = fill(r);
            p.setColor(0xFF000000);
            p.setShader(new LinearGradient(cap.left, 0, cap.left + fw, 0, g[0], g[1], Shader.TileMode.CLAMP));
            c.drawRoundRect(new RectF(cap.left, cap.top, cap.left + fw, cap.bottom), rad, rad, p);
            p.setShader(null);
            float mx = cap.left + cap.width() * elapsed(r) / 100;
            p.setColor(alpha(th.mark, .9f));
            c.drawRoundRect(new RectF(mx - 2, cap.top - rad, mx + 2, cap.bottom + rad), 2, 2, p);
        }

        /** 放图标的玻璃小方块。 */
        void chip(RectF chip, float rad) {
            p.reset();
            p.setAntiAlias(true);
            p.setColor(0x14FFFFFF);
            c.drawRoundRect(chip, rad, rad, p);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(1);
            p.setColor(0x1AFFFFFF);
            c.drawRoundRect(chip, rad, rad, p);
            p.setStyle(Paint.Style.FILL);
        }

        /** 玻璃底：左缘透明，往右渐暗，视频透得过来；左边一道细高光当分隔。from 是左侧已有的压暗程度。 */
        void backdrop(float from) {
            p.reset();
            p.setColor(0xFF000000);
            p.setShader(new LinearGradient(0, 0, 150, 0, alpha(0xFF000000, from), alpha(0xFF000000, Math.max(from, .34f)), Shader.TileMode.CLAMP));
            c.drawRect(0, 0, W, H, p);
            p.setShader(new LinearGradient(0, 0, 0, H, alpha(0xFFFFFFFF, 0), alpha(0xFFFFFFFF, .22f), Shader.TileMode.MIRROR));
            c.drawRect(0, 0, 1.5f, H, p);
            p.setShader(null);
        }

        void glass(RectF r, float radius, float a0, float a1, float stroke) {
            if (glassMode) {  // 透明玻璃：一层薄暗底托住字，白色渐变当反光，上沿亮、下沿暗的描边当厚度
                p.reset();
                p.setAntiAlias(true);
                p.setColor(alpha(0xFF000000, .16f));
                c.drawRoundRect(r, radius, radius, p);
                a0 += .05f;
                a1 += .02f;
                stroke += .12f;
            }
            p.reset();
            p.setAntiAlias(true);
            p.setShader(new LinearGradient(r.left, r.top, r.left + r.width() * .34f, r.bottom,
                    alpha(0xFFFFFFFF, a0), alpha(0xFFFFFFFF, a1), Shader.TileMode.CLAMP));
            c.drawRoundRect(r, radius, radius, p);
            p.setShader(null);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(1);
            p.setColor(alpha(0xFFFFFFFF, stroke));
            if (glassMode) {
                p.setColor(0xFF000000);
                p.setShader(new LinearGradient(0, r.top, 0, r.bottom, alpha(0xFFFFFFFF, stroke + .1f), alpha(0xFFFFFFFF, stroke * .25f), Shader.TileMode.CLAMP));
            }
            c.drawRoundRect(new RectF(r.left + .5f, r.top + .5f, r.right - .5f, r.bottom - .5f), radius, radius, p);
            p.setShader(null);
            p.setStyle(Paint.Style.FILL);
        }
    }
}
