import fileinput
import sys

list_name = sys.argv[1]
z = ""
for line in fileinput.input():
    if not z:
      z = "!list-add-bulk adj-bad "
    pass
