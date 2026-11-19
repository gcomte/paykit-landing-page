from html.parser import HTMLParser
import json,sys,re
class Observe(HTMLParser):
 def __init__(self):
  super().__init__(convert_charrefs=True);self.stack=[];self.text=[];self.links=[];self.codes=[];self.headings=[];self.images=[];self.ids=[];self.lists=[];self.list_items=[];self.open_lists=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs); hidden=bool(self.stack and self.stack[-1]['hidden']) or a.get('aria-hidden')=='true' or 'hidden' in a
  item={'tag':tag,'attrs':a,'hidden':hidden,'text':[]}
  if not hidden:
   if tag in ['p','div','section','header','footer','aside','main','article','nav','figure','figcaption','li','ul'] or re.fullmatch('h[1-6]',tag):self.text.append(' ')
   if 'id' in a:self.ids.append(a['id'])
   if tag=='img':self.images.append([a.get('src'),a.get('alt')])
   if tag in ['ul','ol']:
    index=len(self.lists);self.lists.append({'tag':tag,'parent':self.open_lists[-1] if self.open_lists else None,'items':[]});self.open_lists.append(index)
   if tag=='li':item['list']=self.open_lists[-1]
  if tag not in ['img','input','br','hr','meta','link']:self.stack.append(item)
 def handle_endtag(self,tag):
  if tag in ['img','input','br','hr','meta','link']:return
  assert self.stack and self.stack[-1]['tag']==tag,('nonmatching html',tag,self.stack[-1] if self.stack else None)
  item=self.stack.pop()
  if item['hidden']:return
  text=''.join(item['text']);normal=' '.join(text.split())
  if tag=='a' and 'href' in item['attrs']:self.links.append([item['attrs']['href'],normal])
  if tag=='code':self.codes.append(text)
  if re.fullmatch('h[1-6]',tag):self.headings.append([int(tag[1]),normal])
  if tag=='li':self.lists[item['list']]['items'].append(normal)
  if tag in ['ul','ol']:self.open_lists.pop()
  if tag in ['p','div','section','header','footer','aside','main','article','nav','figure','figcaption','li','ul'] or re.fullmatch('h[1-6]',tag):self.text.append(' ')
 def handle_data(self,data):
  if self.stack and self.stack[-1]['hidden']:return
  self.text.append(data)
  for item in self.stack:
   if not item['hidden']:item['text'].append(data)
 def result(self):return {'text':' '.join(''.join(self.text).split()),'links':self.links,'codes':self.codes,'headings':self.headings,'images':self.images,'ids':sorted(self.ids),'lists':self.lists}
def observe(html):
 p=Observe();p.feed(html);p.close();assert not p.stack;return p.result()
print(json.dumps([observe(html) for html in json.load(sys.stdin)],ensure_ascii=False))
