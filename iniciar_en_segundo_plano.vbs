Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "c:\Users\USER\Downloads\GHS-Script-Free"
WshShell.Run "cmd.exe /c """c:\Users\USER\Downloads\GHS-Script-Free\start.bat"""", 0, False
Set WshShell = Nothing
