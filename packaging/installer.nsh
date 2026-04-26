; installer.nsh — custom NSIS macros for Cairn's electron-builder NSIS installer.
;
; Overrides the default install directory so the plugin lands in Resolve's
; Workflow Integration Plugins folder instead of %ProgramFiles%\Cairn.
;
; Reference: electron-builder NSIS include macros
;   https://www.electron.build/configuration/nsis#custom-nsis-script

; Override $INSTDIR before anything else runs.
!macro customInstallMode
  StrCpy $INSTDIR "$PROGRAMDATA\Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\Cairn"
!macroend

; Validate that DaVinci Resolve is installed before installing the plugin.
; If the target directory's parent doesn't exist, warn the user — don't abort,
; because Resolve may be installed but never launched (plugins dir created on
; first launch).
!macro customInit
  IfFileExists "$PROGRAMDATA\Blackmagic Design\DaVinci Resolve\*.*" resolve_found resolve_missing
  resolve_missing:
    MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION \
      "DaVinci Resolve was not found at$\n$PROGRAMDATA\Blackmagic Design\DaVinci Resolve$\n$\nInstall Cairn anyway? (You can install Resolve later.)" \
      IDOK resolve_found
    Abort
  resolve_found:
!macroend

; Remove the plugin folder on uninstall.
!macro customRemoveFiles
  RMDir /r "$PROGRAMDATA\Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\Cairn"
!macroend
