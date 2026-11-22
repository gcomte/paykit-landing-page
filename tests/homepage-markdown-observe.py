"""Finite SSR/marked observer for this homepage, independent of its exporter."""
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs
import json, re, sys
BLOCKS = set("p div section header footer aside main article nav figure figcaption li ul label option details summary form".split())
VOID = set("img input br hr meta link".split())
def normal(value): return " ".join(value.split())
class Observer(HTMLParser):
 def __init__(self):
  super().__init__(convert_charrefs=True); self.stack=[]; self.text=[]; self.links=[]; self.headings=[]; self.images=[]; self.ids=[]; self.lists=[]; self.open_lists=[]; self.chunks=[]
 def add(self, text):
  self.text.append(text)
  for item in self.stack:
   if not item["hidden"]: item["text"].append(text)
 def handle_starttag(self, tag, attrs):
  a=dict(attrs); hidden=bool(self.stack and self.stack[-1]["hidden"]) or a.get("aria-hidden")=="true" or "hidden" in a or tag in ["svg", "head"]
  item={"tag":tag,"attrs":a,"hidden":hidden,"text":[]}
  if not hidden:
   if tag in BLOCKS or re.fullmatch("h[1-6]",tag) or tag=="br": self.add(" ")
   if "id" in a: self.ids.append(a["id"])
   # Static field guidance is visible in the real empty controls. Observe the
   # actual placeholder attributes, not an exporter-generated expectation.
   if tag in ["input", "textarea"] and a.get("placeholder"): self.add(" " + a["placeholder"])
   if tag=="img" and a.get("alt"):
    src=a.get("src", "")
    if src.startswith("/_next/image?"): src=parse_qs(urlparse(src).query)["url"][0]
    self.images.append([src,a["alt"]])
   if tag=="ul":
    index=len(self.lists); self.lists.append({"parent":self.open_lists[-1] if self.open_lists else None,"items":[]}); self.open_lists.append(index)
   if tag=="li": item["list"]=self.open_lists[-1]
  if tag not in VOID: self.stack.append(item)
 def handle_endtag(self, tag):
  if tag in VOID: return
  assert self.stack and self.stack[-1]["tag"]==tag, (tag,self.stack[-1] if self.stack else None)
  item=self.stack.pop()
  if item["hidden"]: return
  text=normal("".join(item["text"]))
  if tag=="a" and "href" in item["attrs"]: self.links.append([item["attrs"]["href"],text])
  if re.fullmatch("h[1-6]",tag): self.headings.append([int(tag[1]),text])
  if tag=="li": self.lists[item["list"]]["items"].append(text)
  if tag=="ul": self.open_lists.pop()
  if tag in BLOCKS or re.fullmatch("h[1-6]",tag): self.add(" ")
 def handle_data(self, data):
  if not self.stack or not self.stack[-1]["hidden"]:
   self.add(data)
   if normal(data): self.chunks.append(normal(data))
 def result(self):
  assert not self.stack
  result={"text":normal("".join(self.text)),"links":self.links,"headings":self.headings,"images":self.images,"ids":sorted(self.ids),"lists":self.lists}
  if "--chunks" in sys.argv: result["chunks"]=self.chunks
  return result
def observe(html):
 p=Observer(); p.feed(html); p.close(); return p.result()
print(json.dumps([observe(html) for html in json.load(sys.stdin)],ensure_ascii=False))
