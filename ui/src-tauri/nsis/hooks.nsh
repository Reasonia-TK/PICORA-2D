!ifndef PICORA_SHELL_UPDATE_HOOKS_NSH
!define PICORA_SHELL_UPDATE_HOOKS_NSH

; Dedicated icon path avoids the executable's previously cached Shell image.
!define PICORA_SHORTCUT_ICON "icons\picora-shared-ad39f965.ico"

; Notify only this application's existing items after an install or upgrade.
; SHCNE_UPDATEITEM = 0x2000; SHCNF_PATHW | SHCNF_FLUSHNOWAIT = 0x3005.
!macro PICORA_NOTIFY_UPDATED_ITEM path
  IfFileExists "${path}" 0 +2
    System::Call 'shell32::SHChangeNotify(i 0x2000, i 0x3005, w "${path}", p 0)'
!macroend

; Update the icon only. Preserve shortcut target, arguments, working directory,
; AppUserModelID and the user's other shortcut properties.
!macro PICORA_REFRESH_SHORTCUT path
  !insertmacro IsShortcutTarget "${path}" "$INSTDIR\picora-2d.exe"
  Pop $0
  ${If} $0 = 1
    !insertmacro ComHlpr_CreateInProcInstance ${CLSID_ShellLink} ${IID_IShellLink} r0 ""
    ${If} $0 P<> 0
      ${IUnknown::QueryInterface} $0 '("${IID_IPersistFile}",.r1)'
      ${If} $1 P<> 0
        ${IPersistFile::Load} $1 '("${path}", ${STGM_READWRITE}).r2'
        ${If} $2 = 0
          ${IShellLink::SetIconLocation} $0 '("$INSTDIR\${PICORA_SHORTCUT_ICON}",0)'
          ${IPersistFile::Save} $1 '("${path}",1)'
        ${EndIf}
        ${IUnknown::Release} $1 ""
      ${EndIf}
      ${IUnknown::Release} $0 ""
    ${EndIf}
    !insertmacro PICORA_NOTIFY_UPDATED_ITEM "${path}"
  ${EndIf}
!macroend

; The interactive finish page creates its desktop shortcut after POSTINSTALL.
; .onGUIEnd runs after that page, so its icon is updated as well.
Function .onGUIEnd
  Push $0
  Push $1
  Push $2
  Push $3
  IfFileExists "$INSTDIR\${PICORA_SHORTCUT_ICON}" 0 picora_gui_end
    !insertmacro PICORA_REFRESH_SHORTCUT "$DESKTOP\PICORA-2D.lnk"
  picora_gui_end:
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

!macro NSIS_HOOK_POSTINSTALL
  Push $0
  Push $1
  Push $2
  Push $3
  !insertmacro PICORA_NOTIFY_UPDATED_ITEM "$INSTDIR\${MAINBINARYNAME}.exe"

  !if "${STARTMENUFOLDER}" != ""
    !insertmacro PICORA_REFRESH_SHORTCUT "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk"
  !else
    !insertmacro PICORA_REFRESH_SHORTCUT "$SMPROGRAMS\${PRODUCTNAME}.lnk"
  !endif

  !insertmacro PICORA_REFRESH_SHORTCUT "$DESKTOP\${PRODUCTNAME}.lnk"
  !insertmacro PICORA_NOTIFY_UPDATED_ITEM "$INSTDIR\${PICORA_SHORTCUT_ICON}"
  Pop $3
  Pop $2
  Pop $1
  Pop $0
!macroend

!endif
