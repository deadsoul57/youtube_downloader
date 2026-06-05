pip install yt-dlp static-ffmpeg
pyinstaller --onefile --noconsole --version-file=version_file.txt --add-data "%LOCALAPPDATA%\Programs\Python\Python313\Lib\site-packages\static_ffmpeg\bin\win32\*;static_ffmpeg_files" youtube_downloader.py
