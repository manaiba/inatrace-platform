Source tapes/settings.tape
Source tapes/tutorial/prologue.tape
Sleep 3s
Type `inatrace deploy admin demo ana@example.org --company "Demo Cooperative" --create` Sleep 600ms Enter
Wait /Password for the new user/
Sleep 1.5s
Type "Demo-Coop-2026" Sleep 400ms Enter
Wait /The same again/
Sleep 1s
Type "Demo-Coop-2026" Sleep 400ms Enter
Wait@2m /❯$/
