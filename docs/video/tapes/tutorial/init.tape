# Answers as a person types them: the questions come in this order (inatrace/deploy/wizard.py).
Source tapes/settings.tape
Source tapes/tutorial/prologue.tape
Sleep 3s
Type "inatrace deploy init demo" Sleep 600ms Enter
Wait /ssh destination/
Sleep 1.5s
Type "demo" Sleep 500ms Enter
Wait@1m /Install them there/
Sleep 2s
Type "n" Sleep 500ms Enter
Wait /Domain/
Sleep 2s
Enter
Wait@1m /Public IP address/
Sleep 2s
Type "127.0.0.1" Sleep 500ms Enter
Wait /What sits in front/
Sleep 2s
Enter
Wait /Swagger UI/
Sleep 1.5s
Enter
Wait /Backend image/
Sleep 1.5s
Type "ghcr.io/manaiba/inatrace-backend-ci" Sleep 500ms Enter
Wait@1m /Backend version/
Sleep 2s
Type "1" Sleep 500ms Enter
Wait /Frontend image/
Sleep 1.5s
Type "ghcr.io/manaiba/inatrace-frontend-ci" Sleep 500ms Enter
Wait@1m /Frontend version/
Sleep 2s
Type "1" Sleep 500ms Enter
Wait /e-mail sending/
Sleep 1s
Sleep 2s
Enter
Wait /monitoring dashboard/
Sleep 1s
Sleep 2s
Enter
Wait /Go ahead\?/
Sleep 6s
Enter
Wait@1m /❯$/
