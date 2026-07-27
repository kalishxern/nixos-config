#!/usr/bin/env -S awk -f
/Type[[:space:]]+Name\/Value/{sub(/^[[:space:]]+/,"");sub(/^[^[:space:]]+[[:space:]]+/,"");print;start=1;next}start==1{sub(/^[[:space:]]+/,"");sub(/^[^[:space:]]+[[:space:]]+/,"");if($0~/RUNPATH/||$0~/RPATH/||$0~/NEEDED/)print}
