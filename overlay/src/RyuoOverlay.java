import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.PorterDuff;
import android.graphics.RectF;
import android.graphics.SweepGradient;
import android.graphics.Typeface;
import android.os.Looper;
import android.view.Surface;
import android.view.SurfaceControl;

import java.lang.reflect.Method;

/**
 * 原型：不安装 App，用 app_process 以 root 身份跑起来，在屏幕最上层开一个透明图层，
 * 画一个会转的圆环和一个每秒加一的数字，并在左边视频上方显示实时帧率。
 *
 *   adb push overlay.dex /data/local/tmp/
 *   adb shell CLASSPATH=/data/local/tmp/overlay.dex app_process / RyuoOverlay [秒数]
 *
 * 进程退出图层就消失；屏重启后什么都不留。
 */
public final class RyuoOverlay {
    static final int W = 2240, H = 1080, STRIP_X = 1606;

    public static void main(String[] args) throws Exception {
        int seconds = args.length > 0 ? Integer.parseInt(args[0]) : 30;
        Looper.prepareMainLooper();
        // 不经 zygote 启动时系统字体没预载，先手动载入，否则一画字就崩
        Typeface.class.getMethod("loadPreinstalledSystemFontMap").invoke(null);

        SurfaceControl sc = new SurfaceControl.Builder()
                .setName("ryuo-overlay")
                .setBufferSize(W, H)
                .setFormat(PixelFormat.TRANSLUCENT)
                .build();
        SurfaceControl.Transaction t = new SurfaceControl.Transaction();
        // 放到主显示（layer stack 0）上，压在所有窗口最上面
        Method setLayerStack = SurfaceControl.Transaction.class.getMethod("setLayerStack", SurfaceControl.class, int.class);
        setLayerStack.invoke(t, sc, 0);
        t.setLayer(sc, Integer.MAX_VALUE).setVisibility(sc, true).apply();

        Surface surface = new Surface(sc);
        Paint track = stroke(Color.argb(60, 255, 255, 255), 22);
        Paint arc = stroke(Color.WHITE, 22);
        arc.setShader(new SweepGradient(STRIP_X + 317, 540, new int[]{0xFF7A5CFF, 0xFF2DE2C8, 0xFF7A5CFF}, null));
        Paint dot = new Paint(Paint.ANTI_ALIAS_FLAG);
        dot.setColor(Color.WHITE);
        dot.setShadowLayer(18, 0, 0, 0xFF2DE2C8);
        Paint num = text(Color.WHITE, 150, Typeface.DEFAULT_BOLD);
        Paint small = text(0xFF9FB0C3, 34, Typeface.DEFAULT);
        Paint fpsPaint = text(0xFF2DE2C8, 40, Typeface.MONOSPACE);
        Paint bg = new Paint();
        bg.setColor(Color.argb(200, 4, 8, 14));
        RectF ring = new RectF(STRIP_X + 117, 340, STRIP_X + 517, 740);

        long start = System.nanoTime(), lastFps = start;
        int frames = 0, total = 0;
        float fps = 0;
        while ((System.nanoTime() - start) / 1e9 < seconds) {
            double s = (System.nanoTime() - start) / 1e9;
            Canvas c = surface.lockHardwareCanvas();
            c.drawColor(0, PorterDuff.Mode.CLEAR);
            // 右边窄条：半透明底 + 圆环 + 沿环跑的光点 + 数字
            c.drawRect(STRIP_X, 0, W, H, bg);
            c.drawArc(ring, 135, 270, false, track);
            float sweep = (float) (270 * (0.5 + 0.5 * Math.sin(s * 0.8)));
            c.save();
            c.rotate((float) (s * 40 % 360), ring.centerX(), ring.centerY());
            c.drawArc(ring, 135, sweep, false, arc);
            c.restore();
            double a = Math.toRadians(135 + (s * 120 % 270));
            c.drawCircle((float) (ring.centerX() + 200 * Math.cos(a)), (float) (ring.centerY() + 200 * Math.sin(a)), 14, dot);
            String n = String.valueOf((int) s);
            c.drawText(n, ring.centerX() - num.measureText(n) / 2, ring.centerY() + 52, num);
            c.drawText("实时原生绘制 · 原型", STRIP_X + 60, 140, small);
            // 左边视频上方：帧率
            c.drawText(String.format("FPS %.0f  帧 %d", fps, total), 960, 1020, fpsPaint);
            surface.unlockCanvasAndPost(c);

            frames++;
            total++;
            long now = System.nanoTime();
            if (now - lastFps >= 1_000_000_000L) {
                fps = frames * 1e9f / (now - lastFps);
                System.out.println("fps " + Math.round(fps));
                frames = 0;
                lastFps = now;
            }
        }
        t.reparent(sc, null).apply();
        surface.release();
        sc.release();
        System.out.println("done frames=" + total);
    }

    static Paint stroke(int color, float w) {
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(w);
        p.setStrokeCap(Paint.Cap.ROUND);
        p.setColor(color);
        return p;
    }

    static Paint text(int color, float size, Typeface tf) {
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        p.setColor(color);
        p.setTextSize(size);
        p.setTypeface(tf);
        return p;
    }
}
