import tkinter as tk
from tkinter import ttk, messagebox, Toplevel, Canvas
from PIL import Image, ImageTk, ImageGrab
import cv2
import numpy as np
import pyautogui
from pynput import keyboard
import threading
import time
import os

# --- 全局变量 ---
captured_pil_image = None # 捕获的 PIL 图像
linework_pil_image = None # 线稿 PIL 图像
drawing_thread = None # 绘画线程
drawing_active = False # 绘画活动状态标志
app_running = True  # 应用程序运行状态标志，用于优雅地关闭线程
keyboard_listener_thread = None # 键盘监听线程

# --- 截图功能 ---
class ScreenshotSelector:
    def __init__(self, parent, on_complete):
        self.parent = parent
        self.on_complete_callback = on_complete
        self.screen_width = parent.winfo_screenwidth()
        self.screen_height = parent.winfo_screenheight()

        self.selector_window = Toplevel(parent)
        self.selector_window.attributes("-fullscreen", True)
        self.selector_window.attributes("-alpha", 0.3)  # 半透明
        self.selector_window.attributes("-topmost", True) # 窗口置顶
        self.selector_window.configure(bg='grey')
        self.selector_window.overrideredirect(True) # 无边框窗口

        self.canvas = Canvas(self.selector_window, cursor="cross", bg="grey")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.start_x = None
        self.start_y = None
        self.rect = None

        self.canvas.bind("<ButtonPress-1>", self.on_mouse_press)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_release)
        self.selector_window.bind("<Escape>", self.cancel_selection)
        
        # 添加一个标签来指示用户操作
        self.instruction_label = tk.Label(
            self.canvas, 
            text="拖动鼠标选择区域, 按 ESC 取消", 
            bg="white", fg="black", font=("Arial", 14)
        )
        # 将标签放置在顶部中央
        self.canvas.create_window(self.screen_width / 2, 30, window=self.instruction_label)


    def on_mouse_press(self, event):
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y,
                                                 outline='red', width=2)

    def on_mouse_drag(self, event):
        cur_x = self.canvas.canvasx(event.x)
        cur_y = self.canvas.canvasy(event.y)
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_mouse_release(self, event):
        end_x = self.canvas.canvasx(event.x)
        end_y = self.canvas.canvasy(event.y)
        self.selector_window.destroy()

        x1, y1 = min(self.start_x, end_x), min(self.start_y, end_y)
        x2, y2 = max(self.start_x, end_x), max(self.start_y, end_y)

        if x2 - x1 > 0 and y2 - y1 > 0: # 确保是有效区域
            bbox = (x1, y1, x2, y2)
            # 短暂延迟以确保选择器窗口消失后再截图
            self.parent.after(100, lambda: self.grab_screen(bbox))
        else:
            self.on_complete_callback(None)


    def grab_screen(self, bbox):
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            self.on_complete_callback(img)
        except Exception as e:
            print(f"截图失败: {e}")
            messagebox.showerror("截图错误", f"截图失败: {e}")
            self.on_complete_callback(None)

    def cancel_selection(self, event=None):
        self.selector_window.destroy()
        self.on_complete_callback(None)

# --- 图像处理功能 ---
def convert_to_linework(pil_image, canny_threshold1, canny_threshold2, invert_colors=False):
    if pil_image is None:
        return None
    try:
        # 将 PIL 图像转换为 OpenCV 格式
        open_cv_image = np.array(pil_image.convert('RGB'))
        open_cv_image = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2BGR)

        # 灰度化
        gray_image = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)

        # 高斯模糊
        blurred_image = cv2.GaussianBlur(gray_image, (5, 5), 0)

        # Canny 边缘检测
        # 边缘是白色的，背景是黑色的
        canny_edges = cv2.Canny(blurred_image, canny_threshold1, canny_threshold2)

        if invert_colors:
             # 反转颜色：黑线白底
            canny_edges = cv2.bitwise_not(canny_edges)

        # 转换回 PIL 图像
        linework_img = Image.fromarray(canny_edges)
        return linework_img
    except Exception as e:
        print(f"图像处理失败: {e}")
        messagebox.showerror("图像处理错误", f"图像处理失败: {e}")
        return None

# --- 自动绘画功能 ---
def drawing_task(image_to_draw, start_x, start_y, draw_delay, pixel_skip, pyautogui_pause_val): # 添加新参数 pyautogui_pause_val
    global drawing_active
    if image_to_draw is None:
        print("没有线稿图像可供绘制。")
        drawing_active = False
        return

    original_pyautogui_pause = pyautogui.PAUSE # 存储原始的 PAUSE 值
    pyautogui.PAUSE = pyautogui_pause_val  # 使用从GUI控件获取的值

    try:
        pyautogui.FAILSAFE = False # 禁用 pyautogui 的 failsafe 功能
        original_mouse_pos = pyautogui.position() # 存储原始鼠标位置

        # 将 PIL 图像转换为 OpenCV 格式
        if image_to_draw.mode == 'L': # 灰度图
            cv_image = np.array(image_to_draw)
        elif image_to_draw.mode == '1': # 二值图
            # findContours 最适合处理 8 位单通道图像。
            cv_image = np.array(image_to_draw.convert('L'))
        else: # 其他模式的备用方案，转换为灰度图
            print(f"警告: 图像模式为 {image_to_draw.mode}, 将尝试转换为灰度图。")
            cv_image = np.array(image_to_draw.convert('L'))

        # 判断用户是否设置了反转线稿颜色（黑线白底）
        is_inverted_user_setting = app.invert_var.get() if 'app' in globals() and hasattr(globals()['app'], 'invert_var') else False

        # findContours 期望白色对象在黑色背景上。
        # 如果用户设置为“反转”（黑线白底），我们需要为 findContours 反转图像。
        if is_inverted_user_setting:
            cv_image = cv2.bitwise_not(cv_image)
        
        # 对图像进行阈值处理以确保其为二值图像（对 findContours 很重要）
        # Canny 的输出应该已经是大部分二值的，但这能确保这一点。
        # 如果线条是白色 (255)，背景是黑色 (0)
        _, binary_image = cv2.threshold(cv_image, 127, 255, cv2.THRESH_BINARY)

        contours, hierarchy = cv2.findContours(binary_image, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        print(f"开始绘制... 绘制起点: ({start_x}, {start_y}), 找到 {len(contours)} 条轮廓。")
        print(f"绘制逻辑: {'绘制黑色像素对应的轮廓 (用户已反转)' if is_inverted_user_setting else '绘制白色像素对应的轮廓 (标准)'}")
        
        if not contours:
            print("未找到可绘制的轮廓。")
            messagebox.showinfo("提示", "未在线稿中找到可绘制的轮廓。")
            drawing_active = False # 如果没有轮廓，确保停止绘画
            # ... (finally 块的其余部分将处理状态更新) ...
            return # 提前退出

        for contour_idx, contour in enumerate(contours):
            if not drawing_active:
                break
            
            # 将 pixel_skip 应用于轮廓点
            # 这意味着我们不会绘制轮廓上的每一个点，从而使其更快/更粗糙
            simplified_contour_points = []
            if pixel_skip <= 0: # 确保 pixel_skip 至少为 1
                current_pixel_skip = 1
            else:
                current_pixel_skip = pixel_skip

            for i in range(0, len(contour), current_pixel_skip):
                simplified_contour_points.append(contour[i][0]) # 轮廓点是 [[x,y]]

            if len(simplified_contour_points) < 2: # 至少需要两个点才能绘制线段
                continue

            # 移动到轮廓段的起点
            first_point = simplified_contour_points[0]
            pyautogui.moveTo(start_x + first_point[0], start_y + first_point[1], duration=0)
            pyautogui.mouseDown()

            # 沿着轮廓段的后续点拖动
            for i in range(1, len(simplified_contour_points)):
                if not drawing_active:
                    # 如果在拖动过程中绘画被中断，则抬起鼠标按钮
                    pyautogui.mouseUp()
                    break 
                point = simplified_contour_points[i]
                pyautogui.dragTo(start_x + point[0], start_y + point[1], duration=0)
            
            if not drawing_active and i < len(simplified_contour_points) -1 : # 检查是否在内部循环中发生中断
                 pass # mouseUp 已经被调用
            else: # 正常完成段或在内部循环之前中断
                pyautogui.mouseUp()


            if not drawing_active: # 在可能较长的拖动后再次检查
                break

            if draw_delay > 0:
                time.sleep(draw_delay / 1000.0) # 将毫秒转换为秒
            
            # 可选：更频繁地更新状态
            # if 'app' in globals() and hasattr(globals()['app'], 'update_status'):
            #    app.update_status(f"绘画中... 轮廓 {contour_idx + 1}/{len(contours)}")


        if drawing_active: # 绘画自然完成
            messagebox.showinfo("完成", "绘画已完成！")
        else: # 绘画被用户停止
            messagebox.showinfo("停止", "绘画已停止。")

    except Exception as e:
        print(f"绘画过程中发生错误: {e}")
        messagebox.showerror("绘画错误", f"绘画过程中发生错误: {e}")
    finally:
        drawing_active = False
        pyautogui.FAILSAFE = True # 重新启用 failsafe
        pyautogui.PAUSE = original_pyautogui_pause # 恢复原始的 PAUSE 值
        if 'original_mouse_pos' in locals():
             pyautogui.moveTo(original_mouse_pos[0], original_mouse_pos[1], duration=0.1) # 恢复鼠标位置
        if 'app' in globals() and hasattr(globals()['app'], 'update_status'):
            app.update_status("空闲。将鼠标移至绘画区域，按F5开始。")


# --- 键盘监听器 ---
def on_press(key):
    global drawing_active, drawing_thread, linework_pil_image, app
    if not app_running: # 如果应用程序正在关闭，则停止监听器
        return False

    try:
        if key == keyboard.Key.f5:
            if not drawing_active:
                if linework_pil_image is None:
                    messagebox.showwarning("提示", "请先截取并生成线稿图像。")
                    return

                start_x, start_y = pyautogui.position()
                msg = (f"将在当前鼠标位置 ({start_x}, {start_y}) 开始绘画。\n"
                       "请确保目标窗口已准备好接收鼠标点击。\n"
                       "按 ESC 键中途停止。")

                # 强制主窗口置顶，以确保弹窗在其之上且最前
                # 保存主窗口原始的置顶状态
                try:
                    original_topmost_status = app.root.attributes('-topmost')
                    app.root.attributes('-topmost', True)
                except tk.TclError: # 如果根窗口被销毁或未完全初始化，可能会发生这种情况
                    original_topmost_status = False # 默认为 False

                user_confirmed = messagebox.askokcancel("确认开始绘画", msg)

                # 恢复主窗口原始的置顶状态
                try:
                    app.root.attributes('-topmost', original_topmost_status)
                except tk.TclError:
                    pass # 如果窗口不再存在，则忽略

                if user_confirmed:
                    drawing_active = True
                    app.update_status(f"绘画中... 按 ESC 停止。")
                    
                    draw_delay_val = app.draw_delay_scale.get() if hasattr(app, 'draw_delay_scale') else 10
                    # 在此处将 pixel_skip_val 转换为 int
                    pixel_skip_val = int(app.pixel_skip_scale.get()) if hasattr(app, 'pixel_skip_scale') else 1
                    # 获取 pyautogui_pause 的值
                    pyautogui_pause_val = app.pyautogui_pause_scale.get() if hasattr(app, 'pyautogui_pause_scale') else 0.0

                    drawing_thread = threading.Thread(target=drawing_task, 
                                                      args=(linework_pil_image, start_x, start_y, draw_delay_val, pixel_skip_val, pyautogui_pause_val), # 添加新参数
                                                      daemon=True)
                    drawing_thread.start()
            else:
                print("绘画已在进行中。")

        elif key == keyboard.Key.esc:
            if drawing_active:
                print("ESC按下，停止绘画...")
                drawing_active = False # 通知绘画线程停止
                if drawing_thread and drawing_thread.is_alive():
                     # 线程将通过检查 drawing_active 自行停止
                     app.update_status("正在停止绘画...")
                else: # 如果线程以某种方式完成或未运行
                    app.update_status("空闲。将鼠标移至绘画区域，按F5开始。")
            else: # 如果在未绘画时按下 ESC，则可能关闭弹出窗口或应用程序
                if hasattr(app, 'selector') and app.selector and app.selector.selector_window.winfo_exists():
                    app.selector.cancel_selection()


    except Exception as e:
        print(f"键盘监听器错误: {e}")

def start_keyboard_listener():
    global keyboard_listener_thread
    # 以非阻塞方式设置监听器
    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    return listener


# --- GUI 类 ---
class AutoDrawerApp:
    def __init__(self, root_window):
        self.root = root_window
        self.root.title("秋收冬藏") # 应用程序标题
        self.root.geometry("600x750") # 窗口大小
        self.root.protocol("WM_DELETE_WINDOW", self.on_close) # 设置关闭窗口时的回调

        self.selector = None # 用于截图选择器窗口实例

        # --- 样式 ---
        style = ttk.Style()
        style.configure("TButton", padding=6, relief="flat", font=('Helvetica', 10))
        style.configure("TLabel", padding=5, font=('Helvetica', 10))
        style.configure("Header.TLabel", font=('Helvetica', 12, 'bold'))

        # --- 主窗格 ---
        main_pane = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        controls_frame = ttk.Frame(main_pane, padding=10) # 控制区域框架
        main_pane.add(controls_frame, weight=1)
        
        preview_pane = ttk.PanedWindow(main_pane, orient=tk.HORIZONTAL) # 预览区域窗格
        main_pane.add(preview_pane, weight=3)

        # --- 控制框架 ---
        ttk.Label(controls_frame, text="控制面板", style="Header.TLabel").pack(pady=(0,10))

        self.capture_button = ttk.Button(controls_frame, text="1. 截取模仿图片", command=self.select_capture_area)
        self.capture_button.pack(fill=tk.X, pady=5)

        self.process_button = ttk.Button(controls_frame, text="2. 生成线稿", command=self.process_image_button_action, state=tk.DISABLED)
        self.process_button.pack(fill=tk.X, pady=5)
        
        # Canny 参数
        param_frame = ttk.LabelFrame(controls_frame, text="线稿参数 (Canny Edge)")
        param_frame.pack(fill=tk.X, pady=10)

        ttk.Label(param_frame, text="阈值1:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.canny_thresh1_scale = ttk.Scale(param_frame, from_=0, to=255, orient=tk.HORIZONTAL, length=150)
        self.canny_thresh1_scale.set(50)
        self.canny_thresh1_scale.grid(row=0, column=1, padx=5, pady=5)
        self.canny_thresh1_val_label = ttk.Label(param_frame, text="50")
        self.canny_thresh1_scale.config(command=lambda v: self.canny_thresh1_val_label.config(text=f"{float(v):.0f}"))
        self.canny_thresh1_val_label.grid(row=0, column=2, padx=5, pady=5)


        ttk.Label(param_frame, text="阈值2:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.canny_thresh2_scale = ttk.Scale(param_frame, from_=0, to=255, orient=tk.HORIZONTAL, length=150)
        self.canny_thresh2_scale.set(150)
        self.canny_thresh2_scale.grid(row=1, column=1, padx=5, pady=5)
        self.canny_thresh2_val_label = ttk.Label(param_frame, text="150")
        self.canny_thresh2_scale.config(command=lambda v: self.canny_thresh2_val_label.config(text=f"{float(v):.0f}"))
        self.canny_thresh2_val_label.grid(row=1, column=2, padx=5, pady=5)

        self.invert_var = tk.BooleanVar(value=False) # 是否反转颜色变量
        self.invert_checkbox = ttk.Checkbutton(param_frame, text="反转颜色 (黑线白底)", variable=self.invert_var, command=self.on_invert_toggle)
        self.invert_checkbox.grid(row=2, column=0, columnspan=3, pady=5, sticky="w")

        # 绘画参数
        draw_param_frame = ttk.LabelFrame(controls_frame, text="绘画参数")
        draw_param_frame.pack(fill=tk.X, pady=10)

        ttk.Label(draw_param_frame, text="绘制延迟 (ms):").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.draw_delay_scale = ttk.Scale(draw_param_frame, from_=0, to=100, orient=tk.HORIZONTAL, length=150) # 0 到 100 毫秒延迟
        self.draw_delay_scale.set(0) # 默认为 0 毫秒以加快绘制速度
        self.draw_delay_scale.grid(row=0, column=1, padx=5, pady=5)
        self.draw_delay_val_label = ttk.Label(draw_param_frame, text="0")
        self.draw_delay_scale.config(command=lambda v: self.draw_delay_val_label.config(text=f"{float(v):.0f}"))
        self.draw_delay_val_label.grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(draw_param_frame, text="像素跳跃:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.pixel_skip_scale = ttk.Scale(draw_param_frame, from_=1, to=10, orient=tk.HORIZONTAL, length=150) # 跳过 1 到 10 个像素
        self.pixel_skip_scale.set(1) # 默认为 1 (不跳过)
        self.pixel_skip_scale.grid(row=1, column=1, padx=5, pady=5)
        self.pixel_skip_val_label = ttk.Label(draw_param_frame, text="1")
        self.pixel_skip_scale.config(command=lambda v: self.pixel_skip_val_label.config(text=f"{float(v):.0f}"))
        self.pixel_skip_val_label.grid(row=1, column=2, padx=5, pady=5)


        # 新增 PyAutoGUI PAUSE 控件
        ttk.Label(draw_param_frame, text="移动间隔(s):").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.pyautogui_pause_scale = ttk.Scale(draw_param_frame, from_=0.0, to=0.1, orient=tk.HORIZONTAL, length=150)
        self.pyautogui_pause_scale.set(0.0) # 默认值为 0.0
        self.pyautogui_pause_scale.grid(row=2, column=1, padx=5, pady=5)
        self.pyautogui_pause_val_label = ttk.Label(draw_param_frame, text="0.000")
        # 更新标签显示，并格式化为三位小数
        self.pyautogui_pause_scale.config(command=lambda v: self.pyautogui_pause_val_label.config(text=f"{float(v):.3f}"))
        self.pyautogui_pause_val_label.grid(row=2, column=2, padx=5, pady=5)

        self.help_button = ttk.Button(controls_frame, text="帮助/说明", command=self.show_help)
        self.help_button.pack(fill=tk.X, pady=(20,5))

        # --- 预览窗格 ---
        self.original_preview_frame = ttk.LabelFrame(preview_pane, text="原始截图预览")
        preview_pane.add(self.original_preview_frame, weight=1)
        self.original_image_label = ttk.Label(self.original_preview_frame, text="尚未截图")
        self.original_image_label.pack(padx=5, pady=5, expand=True, fill=tk.BOTH)

        self.linework_preview_frame = ttk.LabelFrame(preview_pane, text="线稿预览")
        preview_pane.add(self.linework_preview_frame, weight=1)
        self.linework_image_label = ttk.Label(self.linework_preview_frame, text="尚未生成线稿")
        self.linework_image_label.pack(padx=5, pady=5, expand=True, fill=tk.BOTH)

        # --- 状态栏 ---
        self.status_bar = ttk.Label(self.root, text="空闲。将鼠标移至绘画区域，按F5开始。", relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def update_status(self, message):
        self.status_bar.config(text=message)
        self.root.update_idletasks() # 立即更新界面


    def select_capture_area(self):
        global captured_pil_image, linework_pil_image
        self.update_status("进入截图模式... 请拖动鼠标选择区域。")
        # 截图时隐藏主窗口
        self.root.withdraw() 
        # 确保在主窗口隐藏后创建选择器
        self.root.after(100, self._create_selector)

    def _create_selector(self):
        self.selector = ScreenshotSelector(self.root, self.on_screenshot_complete)

    def on_screenshot_complete(self, image):
        global captured_pil_image, linework_pil_image
        self.root.deiconify() # 重新显示主窗口
        self.selector = None # 清除选择器实例

        if image:
            captured_pil_image = image
            linework_pil_image = None # 清除旧的线稿
            self.display_image(captured_pil_image, self.original_image_label, "原始截图")
            self.display_image(None, self.linework_image_label, "线稿预览") # 清除线稿预览
            self.process_button.config(state=tk.NORMAL) # 启用处理按钮
            self.update_status("截图完成。点击“生成线稿”进行处理。")
        else:
            self.update_status("截图已取消或失败。")
            # 确保按钮状态正确
            if captured_pil_image is None:
                self.process_button.config(state=tk.DISABLED)


    def process_image_button_action(self):
        global linework_pil_image, captured_pil_image
        if captured_pil_image:
            self.update_status("正在生成线稿...")
            canny1 = self.canny_thresh1_scale.get()
            canny2 = self.canny_thresh2_scale.get()
            invert = self.invert_var.get()
            
            # 在单独的线程中运行图像处理以避免 GUI 冻结
            thread = threading.Thread(target=self._process_image_thread, args=(captured_pil_image, canny1, canny2, invert), daemon=True)
            thread.start()
        else:
            messagebox.showwarning("提示", "请先截取一张图片。")
            self.update_status("空闲。请先截图。")

    def _process_image_thread(self, image_to_process, c1, c2, inv):
        global linework_pil_image
        processed_image = convert_to_linework(image_to_process, c1, c2, inv)
        
        # 确保在主线程中更新 GUI
        self.root.after(0, self._update_gui_after_processing, processed_image)

    def _update_gui_after_processing(self, processed_image):
        global linework_pil_image
        if processed_image:
            linework_pil_image = processed_image
            self.display_image(linework_pil_image, self.linework_image_label, "线稿")
            self.update_status("线稿生成成功。将鼠标移至绘画区域，按F5开始绘画。")
        else:
            self.update_status("线稿生成失败。请检查参数或图像。")
            self.display_image(None, self.linework_image_label, "线稿预览") # 清除预览


    def on_invert_toggle(self):
        # 当反转复选框状态改变时，如果已有截图，则重新处理线稿
        if captured_pil_image:
            self.process_image_button_action()


    def display_image(self, pil_image, label_widget, image_type_str):
        if pil_image:
            # 调整图像大小以适应标签，同时保持纵横比
            label_width = label_widget.winfo_width()
            label_height = label_widget.winfo_height()

            if label_width < 2 or label_height < 2: # 标签可能尚未完全渲染
                # 延迟一点再尝试，给标签时间来获取其尺寸
                label_widget.after(50, lambda: self.display_image(pil_image, label_widget, image_type_str))
                return

            img_copy = pil_image.copy()
            img_copy.thumbnail((label_width - 10, label_height - 10), Image.Resampling.LANCZOS) # 减去一些填充
            
            try:
                photo = ImageTk.PhotoImage(image=img_copy)
                label_widget.config(image=photo, text="") # 清除占位符文本
                label_widget.image = photo # 保持对图像的引用，防止被垃圾回收
            except Exception as e:
                print(f"显示 {image_type_str} 时出错: {e}")
                label_widget.config(text=f"{image_type_str} 显示错误")
                label_widget.image = None
        else:
            label_widget.config(image=None, text=f"无{image_type_str}")
            label_widget.image = None # 清除引用

    def show_help(self):
        help_text = """
        欢迎使用 秋收冬藏 - 自动绘画助手！

        使用步骤:
        1. 点击 "1. 截取模仿图片" 按钮。
           - 屏幕会变暗，鼠标会变成十字准星。
           - 拖动鼠标选择您想要模仿绘画的区域。
           - 松开鼠标完成截图，或按 ESC 键取消。
        2. 调整 "线稿参数" (可选):
           - 阈值1 & 阈值2: 控制边缘检测的敏感度。较低的值会检测更多细节。
           - 反转颜色: 如果原始图片是深色背景浅色线条，勾选此项。默认处理浅色背景深色线条。
        3. 点击 "2. 生成线稿" 按钮。
           - 程序会根据参数将截图转换为黑白线稿。
           - 线稿会显示在右侧的 "线稿预览" 区域。
        4. 调整 "绘画参数" (可选):
           - 绘制延迟: 每条轮廓绘制完成后的等待时间 (毫秒)。设为0以最快速度绘制。
           - 像素跳跃: 绘制时跳过的像素点数。值越大，绘制越快但越粗糙。
        5. 准备绘画:
           - 将鼠标指针移动到您希望开始绘画的目标应用程序窗口的画布区域。
        6. 开始绘画:
           - 按下键盘上的 F5 键。
           - 会有一个确认对话框，点击 "确定" 开始。
        7. 停止绘画:
           - 在绘画过程中，随时可以按下键盘上的 ESC 键来停止。

        提示:
        - 确保目标绘画程序窗口是激活的，并且有足够的空间进行绘画。
        - 绘画速度受 "绘制延迟" 和 "像素跳跃" 参数以及图像复杂度的影响。
        - 如果线稿效果不佳，尝试调整 "线稿参数" 并重新生成。
        - "反转颜色" 对于处理扫描的白色纸张上的黑色笔迹非常有用。
        """
        messagebox.showinfo("帮助/说明", help_text)

    def on_close(self):
        global drawing_active, app_running, keyboard_listener_thread
        if drawing_active:
            if messagebox.askyesno("退出", "绘画正在进行中，确定要退出吗？"):
                drawing_active = False # 尝试停止绘画
                app_running = False # 设置标志以停止监听器
                if drawing_thread and drawing_thread.is_alive():
                    drawing_thread.join(timeout=1) # 等待线程一小段时间
                self.root.destroy()
            else:
                return # 不退出
        else:
            app_running = False # 设置标志以停止监听器
            self.root.destroy()

        # 确保键盘监听器线程被正确停止
        # pynput 的 listener.stop() 应该从监听器线程本身或另一个线程调用
        # 但由于我们设置了 app_running = False, on_press 会返回 False, 这将停止监听器
        # 如果监听器是守护线程，它会在主线程退出时自动结束。
        # 如果不是守护线程，并且 listener.join() 不起作用，可能需要更复杂的机制。
        # 对于此应用，daemon=True 应该足够了。

# --- 主程序入口 ---
if __name__ == "__main__":
    root = tk.Tk()
    app = AutoDrawerApp(root) # 将 app 实例赋给全局变量，以便其他函数访问
    
    # 启动键盘监听器
    # 将监听器作为守护线程运行，这样当主程序退出时它也会退出
    keyboard_listener_thread = threading.Thread(target=start_keyboard_listener, daemon=True)
    keyboard_listener_thread.start()

    root.mainloop()

    # 确保在退出时 app_running 为 False，以帮助任何仍在运行的线程（如键盘监听器）干净地退出
    app_running = False
    if keyboard_listener_thread and keyboard_listener_thread.is_alive():
        # 通常不需要显式 join 守护线程，但如果需要确保它在主程序逻辑完全结束后才停止，可以这样做
        # print("等待键盘监听器线程结束...")
        # keyboard.Listener.stop() # 尝试停止监听器（如果从外部线程调用有效）
        pass # 守护线程会自动处理