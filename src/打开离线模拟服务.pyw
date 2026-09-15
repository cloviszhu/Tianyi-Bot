import sys
from wechat_gallery_bot.management.guest_gui import main

sys.argv = [sys.argv[0], "--simulate"]
main()
