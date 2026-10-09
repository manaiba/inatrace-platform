Source tapes/settings.tape
Source tapes/tutorial/prologue.tape
Sleep 3s
Type "inatrace deploy backup create demo" Sleep 600ms Enter
Wait@3m /❯$/
Sleep 3s
Type "inatrace deploy backup list demo" Sleep 600ms Enter
Wait@1m /❯$/
