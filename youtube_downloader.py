import os
import sys
import threading
import queue
import winreg  # Модуль для роботи з реєстром Windows
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog
from tkinter import ttk
import yt_dlp
import static_ffmpeg

# Константа шляху в реєстрі
REG_PATH = r"Software\YouTubeDownloader"

def init_ffmpeg():
    """Ініціалізація або розпакування FFmpeg залежно від режиму запуску"""
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        ffmpeg_dir = os.path.join(base_path, 'static_ffmpeg_files')
        os.environ["PATH"] += os.pathsep + ffmpeg_dir
    else:
        static_ffmpeg.add_paths()

class UserCancelledException(Exception):
    """Кастомне виключення для переривання yt-dlp"""
    pass

class CancelLogger:
    """Логер для yt-dlp, який перевіряє прапорець скасування під час аналізу/логів"""
    def __init__(self, cancel_event):
        self.cancel_event = cancel_event

    def debug(self, msg):
        self.check_cancel()

    def info(self, msg):
        self.check_cancel()

    def warning(self, msg):
        self.check_cancel()

    def error(self, msg):
        self.check_cancel()

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise UserCancelledException("Завантаження скасовано користувачем.")

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

        # Потокобезпечна черга, події та прапорці
        self.gui_queue = queue.Queue()
        self.dialog_event = threading.Event()
        self.cancel_event = threading.Event()
        self.dialog_result = None
        self.is_downloading = False

        # Зчитуємо параметри з реєстру
        self.saved_settings = self.load_settings()

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

        default_dir = self.saved_settings.get("SavePath")
        if not default_dir or not os.path.exists(default_dir):
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
        
        saved_quality = self.saved_settings.get("Quality")
        if saved_quality in self.quality_options:
            self.quality_combobox.set(saved_quality)
        else:
            self.quality_combobox.current(0)
            
        self.quality_combobox.pack(pady=2)

        # --- Опція збереження вихідних файлів ---
        saved_keep_files = self.saved_settings.get("KeepFiles", False)
        self.keep_files_var = tk.BooleanVar(value=saved_keep_files)
        self.keep_files_cb = tk.Checkbutton(
            root, 
            text="Зберігати окремі вихідні файли (відео та аудіо) після склейки", 
            variable=self.keep_files_var,
            font=("Arial", 9)
        )
        self.keep_files_cb.pack(pady=(5, 0))

        # --- Блок індикатора прогресу ---
        self.percent_label = tk.Label(root, text="Готовність: 0%", font=("Arial", 9))
        self.percent_label.pack(pady=(5, 2))

        self.progress = ttk.Progressbar(root, orient="horizontal", length=450, mode="determinate")
        self.progress.pack(pady=2)

        # --- Кнопка дій (Скачати / Скасувати) ---
        self.download_btn = tk.Button(root, text="Скачати", font=("Arial", 10, "bold"), 
                                      bg="#4CAF50", fg="white", padx=20, pady=5, command=self.toggle_download)
        self.download_btn.pack(pady=10)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.check_queue()

    def load_settings(self):
        """Зчитування налаштувань із реєстру Windows"""
        settings = {}
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
            
            try:
                settings["SavePath"], _ = winreg.QueryValueEx(key, "SavePath")
            except FileNotFoundError:
                pass

            try:
                settings["Quality"], _ = winreg.QueryValueEx(key, "Quality")
            except FileNotFoundError:
                pass

            try:
                val, _ = winreg.QueryValueEx(key, "KeepFiles")
                settings["KeepFiles"] = bool(val)
            except FileNotFoundError:
                pass

            winreg.CloseKey(key)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Помилка зчитування з реєстру: {e}")
            
        return settings

    def save_settings(self):
        """Збереження поточних налаштувань у реєстр Windows"""
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
            
            winreg.SetValueEx(key, "SavePath", 0, winreg.REG_SZ, self.dir_entry.get().strip())
            winreg.SetValueEx(key, "Quality", 0, winreg.REG_SZ, self.quality_combobox.get())
            winreg.SetValueEx(key, "KeepFiles", 0, winreg.REG_DWORD, int(self.keep_files_var.get()))
            
            winreg.CloseKey(key)
        except Exception as e:
            print(f"Помилка збереження в реєстр: {e}")

    def on_closing(self):
        """Обробник закриття вікна"""
        if self.is_downloading:
            self.cancel_event.set()
            self.dialog_event.set()
        self.save_settings()
        self.root.destroy()

    def browse_folder(self):
        selected_dir = filedialog.askdirectory(initialdir=self.dir_entry.get())
        if selected_dir:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, selected_dir)
            self.save_settings()

    def toggle_download(self):
        """Перемикач для кнопки Скачати/Скасувати"""
        if self.is_downloading:
            # Натиснуто кнопка Скасувати
            self.cancel_event.set()
            self.dialog_event.set()  # Розблоковує потік, якщо той чекає на відповідь у діалозі
            self.percent_label.config(text="Зупинка процесу...")
            self.download_btn.config(state=tk.DISABLED, bg="#9E9E9E")
        else:
            self.start_download_thread()

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
        # Перевірка на переривання
        if self.cancel_event.is_set():
            raise UserCancelledException("Завантаження скасовано користувачем.")

        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                percent = (downloaded / total) * 100
                self.gui_queue.put({"action": "update_ui", "percent": percent, "text": f"Готовність: {percent:.1f}%"})
        elif d['status'] == 'finished':
            self.gui_queue.put({"action": "update_ui", "percent": 100, "text": "Обробка та склейка відео..."})

    def start_download_thread(self):
        self.save_settings()

        url = self.url_entry.get().strip()
        save_dir = self.dir_entry.get().strip()
        keep_files = self.keep_files_var.get()
        
        selected_text = self.quality_combobox.get()
        format_str = self.quality_options.get(selected_text, "bestvideo+bestaudio/best")

        if not url:
            messagebox.showwarning("Увага", "Будь ласка, введіть посилання на відео!")
            return
        if not os.path.exists(save_dir):
            messagebox.showwarning("Увага", "Вказана папка не існує! Виберіть інший шлях.")
            return

        self.cancel_event.clear()
        self.is_downloading = True

        self.progress['value'] = 0
        self.percent_label.config(text="Аналіз посилання...")
        
        # Трансформуємо кнопку у стан "Скасувати"
        self.download_btn.config(text="Скасувати", bg="#F44336", state=tk.NORMAL)
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

            meta_opts = {
                'format': format_str,
                'outtmpl': os.path.join(save_dir, '%(title)s.%(ext)s'),
                'no_color': True,
                'logger': CancelLogger(self.cancel_event),
                'socket_timeout': 10  # Таймаут мережевих запитів
            }
            final_filename_template = '%(title)s.%(ext)s'

            try:
                # 1. Отримання інформації про відео
                with yt_dlp.YoutubeDL(meta_opts) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                    real_output_path = ydl.prepare_filename(info)
                    
                base_name = os.path.basename(real_output_path)
                title, ext = os.path.splitext(base_name)

                if self.cancel_event.is_set():
                    raise UserCancelledException("Завантаження скасовано користувачем.")

                # 2. Перевірка наявності файлу
                if os.path.exists(real_output_path):
                    self.dialog_event.clear()
                    self.gui_queue.put({"action": "ask_overwrite", "filename": real_output_path})
                    
                    self.dialog_event.wait()
                    
                    if self.cancel_event.is_set():
                        raise UserCancelledException("Завантаження скасовано користувачем.")
                        
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
                        
                        if self.cancel_event.is_set():
                            raise UserCancelledException("Завантаження скасовано користувачем.")
                            
                        new_title = self.dialog_result
                        
                        if new_title and new_title.strip():
                            clean_title = new_title.strip()
                            for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
                                clean_title = clean_title.replace(char, '_')
                            final_filename_template = f"{clean_title}.%(ext)s"
                        else:
                            raise UserCancelledException("Перейменування скасовано користувачем.")
                    else:
                        raise UserCancelledException("Завантаження скасовано користувачем.")

                # 3. Налаштування та завантаження
                ydl_opts = {
                    'format': format_str,
                    'outtmpl': os.path.join(save_dir, final_filename_template),
                    'no_color': True,
                    'progress_hooks': [self.progress_hook],
                    'logger': CancelLogger(self.cancel_event),
                    'keepvideo': keep_files,
                    'socket_timeout': 10
                }

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

            except UserCancelledException:
                log_file.write("\nОперацію скасовано користувачем.\n")
                self.gui_queue.put({"action": "update_ui", "percent": 0, "text": "Завантаження скасовано"})
                
            except Exception as e:
                log_file.write(f"\nПомилка виконання: {str(e)}\n")
                self.gui_queue.put({"action": "update_ui", "percent": 0, "text": "Помилка завантаження!"})
                self.root.after(0, lambda: messagebox.showerror("Помилка", f"Сталася помилка або невірне посилання.\nДеталі у файлі:\n{log_file_path}"))
                
            finally:
                self.is_downloading = False
                self.root.after(0, lambda: self.download_btn.config(text="Скачати", bg="#4CAF50", state=tk.NORMAL))
                self.root.after(0, lambda: self.browse_btn.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.keep_files_cb.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.quality_combobox.config(state="readonly"))
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__

if __name__ == "__main__":
    root = tk.Tk()
    app = YoutubeDownloaderGUI(root)
    root.mainloop()
