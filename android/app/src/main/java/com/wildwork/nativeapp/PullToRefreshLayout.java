package com.wildwork.nativeapp;

import android.content.Context;
import android.util.AttributeSet;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.ProgressBar;

/**
 * 零依赖下拉刷新容器。
 *
 * 本项目刻意不引入 AndroidX（SwipeRefreshLayout 只存在于 androidx），所以自己实现最小版：
 * 只在内容已滚到顶部、且确实是下拉时才接管手势；否则一律放行给 WebView，
 * 不与网页自身的滚动 / overscroll 打架。
 */
public class PullToRefreshLayout extends FrameLayout {

    public interface OnRefreshListener {
        void onRefresh();
    }

    private final ProgressBar spinner;
    private final int touchSlop;
    private final float triggerPx;

    private View content;
    private OnRefreshListener listener;
    private float startY;
    private boolean dragging;
    private boolean refreshing;

    public PullToRefreshLayout(Context c) {
        this(c, null);
    }

    public PullToRefreshLayout(Context c, AttributeSet a) {
        super(c, a);
        touchSlop = ViewConfiguration.get(c).getScaledTouchSlop();
        triggerPx = 64f * c.getResources().getDisplayMetrics().density;

        spinner = new ProgressBar(c);
        spinner.setIndeterminate(true);
        spinner.setVisibility(GONE);
        LayoutParams lp = new LayoutParams(dp(c, 26), dp(c, 26));
        lp.gravity = android.view.Gravity.CENTER_HORIZONTAL | android.view.Gravity.TOP;
        lp.topMargin = dp(c, 10);
        addView(spinner, lp);
    }

    private static int dp(Context c, int v) {
        return (int) (v * c.getResources().getDisplayMetrics().density);
    }

    /** WebView 作为内容放最底层；spinner 叠加在其之上绘制。 */
    public void attach(View v) {
        content = v;
        addView(v, 0, new LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
    }

    public void setOnRefreshListener(OnRefreshListener l) {
        listener = l;
    }

    public void setRefreshing(boolean on) {
        refreshing = on;
        if (on) {
            spinner.animate().cancel();
            spinner.setVisibility(VISIBLE);
            spinner.setAlpha(1f);
            spinner.setTranslationY(triggerPx);
        } else {
            spinner.animate().alpha(0f).translationY(0f).setDuration(160)
                    .withEndAction(() -> spinner.setVisibility(GONE)).start();
        }
    }

    @Override
    public boolean onInterceptTouchEvent(MotionEvent ev) {
        if (refreshing) {
            return true; // 刷新进行中（很短），先吞掉避免重复触发
        }
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                startY = ev.getY();
                dragging = false;
                break;
            case MotionEvent.ACTION_MOVE:
                float dy = ev.getY() - startY;
                if (dy > touchSlop && content != null && !content.canScrollVertically(-1)) {
                    dragging = true;
                    return true;
                }
                break;
            default:
                break;
        }
        return super.onInterceptTouchEvent(ev);
    }

    @Override
    public boolean onTouchEvent(MotionEvent ev) {
        if (!dragging) {
            return super.onTouchEvent(ev);
        }
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_MOVE: {
                float dy = Math.max(0f, ev.getY() - startY) * 0.5f;
                spinner.setVisibility(VISIBLE);
                spinner.setTranslationY(Math.min(triggerPx * 1.3f, dy));
                spinner.setAlpha(Math.min(1f, dy / triggerPx));
                return true;
            }
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL: {
                float dy = ev.getActionMasked() == MotionEvent.ACTION_CANCEL
                        ? 0f : Math.max(0f, ev.getY() - startY) * 0.5f;
                dragging = false;
                if (dy >= triggerPx && listener != null) {
                    setRefreshing(true);
                    listener.onRefresh();
                } else {
                    setRefreshing(false);
                }
                return true;
            }
            default:
                return true;
        }
    }
}
