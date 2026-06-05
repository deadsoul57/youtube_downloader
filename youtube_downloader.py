import os
import sys
import threading
import queue
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog
from tkinter import ttk
import yt_dlp
import static_ffmpeg

def init_ffmpeg():
    """Ініціалізація або розпакування FFmpeg залежно від режиму запуску"""
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        ffmpeg_dir = os.path.join(base_path, 'static_ffmpeg_files')
        os.environ["PATH"] += os.pathsep + ffmpeg_dir
    else:
        static_ffmpeg.add_paths()

class YoutubeDownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Downloader")
        
        # Розміри вікна
        window_width = 550
        window_height = 370
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        center_x = int(screen_width / 2 - window_width / 2)
        center_y = int(screen_height / 2 - window_height / 2)
        self.root.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        self.root.resizable(False, False)

        init_ffmpeg()

        # Потокобезпечна черга та події для синхронізації діалогів
        self.gui_queue = queue.Queue()
        self.dialog_event = threading.Event()
        self.dialog_result = None

        # --- Блок введення посилання ---
        self.label = tk.Label(root, text="Вставте посилання на відео YouTube:", font=("Arial", 10, "bold"))
        self.label.pack(pady=(15, 2))

        self.url_entry = tk.Entry(root, width=65, font=("Arial", 10))
        self.url_entry.pack(pady=5)
        self.url_entry.focus()

        # --- Блок вибору папки для збереження ---
        self.dir_label = tk.Label(root, text="Папка для збереження:", font=("Arial", 10, "bold"))
        self.dir_label.pack(pady=(5, 2))

        self.dir_frame = tk.Frame(root)
        self.dir_frame.pack(pady=2)

        default_dir = os.path.join(os.path.expanduser('~'), 'Downloads')
        if not os.path.exists(default_dir):
            default_dir = os.getcwd()

        self.dir_entry = tk.Entry(self.dir_frame, width=50, font=("Arial", 10))
        self.dir_entry.insert(0, default_dir)
        self.dir_entry.pack(side=tk.LEFT, padx=(0, 5))

        self.browse_btn = tk.Button(self.dir_frame, text="Огляд...", font=("Arial", 9), command=self.browse_folder)
        self.browse_btn.pack(side=tk.LEFT)

        # --- Блок вибору якості відео ---
        self.quality_label = tk.Label(root, text="Якість відео:", font=("Arial", 10, "bold"))
        self.quality_label.pack(pady=(5, 2))

        self.quality_options = {
            "Найкраща якість": "bestvideo+bestaudio/best",
            "1080p (Full HD)": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "720p (HD)": "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "360p": "bestvideo[height<=360]+bestaudio/best[height<=360]",
            "Тільки аудіо (MP3/M4A)": "bestaudio/best"
        }
        
        self.quality_combobox = ttk.Combobox(root, values=list(self.quality_options.keys()), state="readonly", width=30, font=("Arial", 10))
        self.quality_combobox.current(0)  # За замовчуванням: Найкраща якість
        self.quality_combobox.pack(pady=2)

        # --- Опція збереження вихідних файлів відео та звуку ---
        self.keep_files_var = tk.BooleanVar(value=False)
        self.keep_files_cb = tk.Checkbutton(
            root, 
            text="Зберігати окремі вихідні файли (відео та аудіо) після склейки", 
            variable=self.keep_files_var,
            font=("Arial", 9)
        )
        self.keep_files_cb.pack(pady=(5, 0))

        # --- Блок графічного індикатора прогресу ---
        self.percent_label = tk.Label(root, text="Готовність: 0%", font=("Arial", 9))
        self.percent_label.pack(pady=(5, 2))

        self.progress = ttk.Progressbar(root, orient="horizontal", length=450, mode="determinate")
        self.progress.pack(pady=2)

        # --- Кнопка старту завантаження ---
        self.download_btn = tk.Button(root, text="Скачати", font=("Arial", 10, "bold"), 
                                      bg="#4CAF50", fg="white", padx=20, pady=5, command=self.start_download_thread)
        self.download_btn.pack(pady=10)

        # Запуск моніторингу черги повідомлень (кожні 100 мс)
        self.check_queue()

    def browse_folder(self):
        selected_dir = filedialog.askdirectory(initialdir=self.dir_entry.get())
        if selected_dir:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, selected_dir)

    def check_queue(self):
        """Головний потік GUI розбирає чергу завдань від фонового потоку"""
        try:
            while True:
                task = self.gui_queue.get_nowait()
                action = task.get("action")
                
                if action == "update_ui":
                    self.progress['value'] = task['percent']
                    self.percent_label.config(text=task['text'])
                    
                elif action == "ask_overwrite":
                    filename = task['filename']
                    res = messagebox.askyesnocancel(
                        "Файл вже існує", 
                        f"Файл '{os.path.basename(filename)}' вже існує в цій папці.\n\n"
                        "Бажаєте ПЕРЕЗАПИСАТИ його?\n"
                        "[Так] - Перезаписати\n"
                        "[Ні] - Перейменувати\n"
                        "[Скасувати] - Зупинити завантаження"
                    )
                    self.dialog_result = res
                    self.dialog_event.set()
                    
                elif action == "ask_name":
                    current_name = task['current_name']
                    name = simpledialog.askstring(
                        "Нове ім'я файлу", 
                        "Введіть назву для відео (без розширення):", 
                        initialvalue=current_name
                    )
                    self.dialog_result = name
                    self.dialog_event.set()
                    
                self.gui_queue.task_done()
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.check_queue)

    def progress_hook(self, d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                percent = (downloaded / total) * 100
                self.gui_queue.put({"action": "update_ui", "percent": percent, "text": f"Готовність: {percent:.1f}%"})
        elif d['status'] == 'finished':
            self.gui_queue.put({"action": "update_ui", "percent": 100, "text": "Обробка та склейка відео..."})

    def start_download_thread(self):
        url = self.url_entry.get().strip()
        save_dir = self.dir_entry.get().strip()
        keep_files = self.keep_files_var.get()
        
        # Отримуємо внутрішній формат yt-dlp відповідно до вибраного пункту
        selected_text = self.quality_combobox.get()
        format_str = self.quality_options.get(selected_text, "bestvideo+bestaudio/best")

        if not url:
            messagebox.showwarning("Увага", "Будь ласка, введіть посилання на відео!")
            return
        if not os.path.exists(save_dir):
            messagebox.showwarning("Увага", "Вказана папка не існує! Виберіть інший шлях.")
            return

        self.progress['value'] = 0
        self.percent_label.config(text="Аналіз посилання...")
        self.download_btn.config(state=tk.DISABLED, bg="#9E9E9E")
        self.browse_btn.config(state=tk.DISABLED)
        self.keep_files_cb.config(state=tk.DISABLED)
        self.quality_combobox.config(state=tk.DISABLED)
        
        threading.Thread(target=self.process_and_download, args=(url, save_dir, keep_files, format_str), daemon=True).start()

    def process_and_download(self, video_url, save_dir, keep_files, format_str):
        exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.getcwd()
        log_file_path = os.path.join(exe_dir, "downloader_log.txt")

        with open(log_file_path, "w", encoding="utf-8") as log_file:
            sys.stdout = log_file
            sys.stderr = log_file

            # Конфігурація для визначення правильного розширення з урахуванням вибраної якості
            meta_opts = {
                'format': format_str,
                'outtmpl': os.path.join(save_dir, '%(title)s.%(ext)s'),
                'no_color': True
            }
            final_filename_template = '%(title)s.%(ext)s'

            try:
                # 1. Вираховуємо точне фінальне ім'я файлу через інструменти yt-dlp
                with yt_dlp.YoutubeDL(meta_opts) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                    real_output_path = ydl.prepare_filename(info)
                    
                base_name = os.path.basename(real_output_path)
                title, ext = os.path.splitext(base_name)

                # 2. Перевіряємо фізичну наявність файлу на диску
                if os.path.exists(real_output_path):
                    self.dialog_event.clear()
                    self.gui_queue.put({"action": "ask_overwrite", "filename": real_output_path})
                    
                    self.dialog_event.wait()
                    user_choice = self.dialog_result
                    
                    if user_choice is True:  # Перезаписати
                        try:
                            os.remove(real_output_path)
                        except Exception as err:
                            log_file.write(f"Помилка видалення файлу перед перезаписом: {err}\n")
                    elif user_choice is False:  # Перейменувати
                        self.dialog_event.clear()
                        self.gui_queue.put({"action": "ask_name", "current_name": title})
                        
                        self.dialog_event.wait()
                        new_title = self.dialog_result
                        
                        if new_title and new_title.strip():
                            clean_title = new_title.strip()
                            for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
                                clean_title = clean_title.replace(char, '_')
                            final_filename_template = f"{clean_title}.%(ext)s"
                        else:
                            raise Exception("Перейменування скасовано користувачем.")
                    else:  # Скасувати
                        raise Exception("Завантаження скасовано користувачем.")

                # 3. Налаштування для запуску фактичного скачування
                ydl_opts = {
                    'format': format_str,
                    'outtmpl': os.path.join(save_dir, final_filename_template),
                    'no_color': True,
                    'progress_hooks': [self.progress_hook],
                    'keepvideo': keep_files,
                }

                # Додаткові параметри для вилучення аудіо, якщо обрано режим "Тільки аудіо"
                if "bestaudio" in format_str and "bestvideo" not in format_str:
                    ydl_opts['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }]

                self.gui_queue.put({"action": "update_ui", "percent": 0, "text": "Завантаження..."})
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
                
                self.gui_queue.put({"action": "update_ui", "percent": 100, "text": "Завантаження успішне!"})
                
                self.root.after(0, lambda: messagebox.showinfo("Успіх", f"Файл успішно збережено в папку:\n{save_dir}"))
                self.root.after(0, lambda: self.url_entry.delete(0, tk.END))

            except Exception as e:
                log_file.write(f"\nПомилка виконання: {str(e)}\n")
                self.gui_queue.put({"action": "update_ui", "percent": 0, "text": "Скасовано або Помилка!"})
                if "скасовано користувачем" not in str(e):
                    self.root.after(0, lambda: messagebox.showerror("Помилка", f"Сталася помилка. Деталі у файлі:\n{log_file_path}"))
            finally:
                self.root.after(0, lambda: self.download_btn.config(state=tk.NORMAL, bg="#4CAF50"))
                self.root.after(0, lambda: self.browse_btn.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.keep_files_cb.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.quality_combobox.config(state="readonly"))
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__

if __name__ == "__main__":
    root = tk.Tk()
    app = YoutubeDownloaderGUI(root)
    root.mainloop()
