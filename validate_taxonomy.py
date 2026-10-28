#!/usr/bin/env python3
"""Validate BuyNSellem taxonomy proposals. Tries the live public API first; falls back to prompt baseline if unreachable."""
from __future__ import annotations
import argparse, json, re, sys, urllib.request, urllib.error
from pathlib import Path
from collections import Counter, defaultdict

BASE = Path(__file__).resolve().parent
API_CATEGORIES = 'https://api.buynsellem.com/api/public/categories'
API_TAGS = 'https://api.buynsellem.com/api/public/tags'

ROOTS = {
'vehicles':('Véhicules','Vehicles'), 'real-estate':('Immobilier','Real estate'),
'electronics':('Électronique','Electronics'), 'home-garden':('Maison et jardin','Home & garden'),
'fashion':('Mode','Fashion'), 'jobs-services':('Emploi et services','Jobs & services'),
'leisure-sports':('Loisirs et sport','Leisure & sports'), 'kids-baby':('Bébés et enfants','Kids & baby'),
'other':('Autres','Other')}
CHILDREN = {
'vehicles': {'cars':('Voitures','Cars'),'motorcycles':('Motos','Motorcycles'),'trucks-utility':('Camions et utilitaires','Trucks & utility vehicles')},
'real-estate': {'apartments':('Appartements','Apartments'),'houses':('Maisons','Houses'),'commercial-property':('Immobilier commercial','Commercial property'),'land':('Terrains','Land')},
'electronics': {'phones':('Téléphones','Phones'),'computers':('Ordinateurs','Computers'),'tvs-monitors':('TV et moniteurs','TVs & monitors'),'gaming':('Jeux vidéo','Gaming')},
'home-garden': {'furniture':('Meubles','Furniture'),'appliances':('Électroménager','Appliances'),'garden-diy':('Jardin et bricolage','Garden & DIY')},
'fashion': {'mens-clothing':('Vêtements homme','Men’s clothing'),'womens-clothing':('Vêtements femme','Women’s clothing'),'watches-jewelry':('Montres et bijoux','Watches & jewelry')},
'jobs-services': {'job-offers':('Offres d’emploi','Job offers'),'services':('Services','Services')},
'leisure-sports': {'sports-equipment':('Équipement sportif','Sports equipment'),'books-media':('Livres et médias','Books & media'),'travel-vacation':('Voyages et vacances','Travel & vacation')},
}
EXISTING_CATS = set(ROOTS)
PARENT = {slug:None for slug in ROOTS}
NAMES = {slug:{'fr':v[0],'en':v[1]} for slug,v in ROOTS.items()}
for parent, kids in CHILDREN.items():
    for slug, val in kids.items():
        EXISTING_CATS.add(slug); PARENT[slug]=parent; NAMES[slug]={'fr':val[0],'en':val[1]}

KNOWN_TAGS = {'negotiable','swap','delivery','urgent','bundle','warranty','receipt','first-owner','new-with-tags','demo','visit','full-time','part-time','remote'}
# Only baseline attribute facts explicitly supplied in the brief are hard-coded here.
KNOWN_EXISTING_ATTRIBUTES = {'condition'}
KNOWN_ATTRS_BY_CATEGORY = defaultdict(set)
for root in ROOTS: KNOWN_ATTRS_BY_CATEGORY[root].add('condition')
KNOWN_ATTRS_BY_CATEGORY['cars'].add('make')
SLUG_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
ATTR_SLUG_RE = SLUG_RE
VALUE_RE = re.compile(r'^[a-z0-9]+(?:_[a-z0-9]+)*$')
ALLOWED_TYPES = {'text','number','select','boolean','date'}
ALLOWED_FORM_TYPES = {'product','service','job','rental','generic'}
ALLOWED_FORM_MODES = {'auto','required','optional','hidden'}

class Checks:
    def __init__(self): self.errors=[]; self.warnings=[]; self.passed=[]
    def err(self, m): self.errors.append(m)
    def warn(self, m): self.warnings.append(m)
    def ok(self, m): self.passed.append(m)

def read_json(filename):
    try:
        return json.loads((BASE/filename).read_text(encoding='utf-8'))
    except Exception as e:
        raise RuntimeError(f'{filename}: JSON illisible: {e}')

def fetch_json(url, timeout=4):
    req=urllib.request.Request(url, headers={'User-Agent':'BuyNSellem-taxonomy-validator/1.0','Accept':'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def walk_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values(): yield from walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj: yield from walk_dicts(v)

def slugged_records(obj, must_have='slug'):
    records=[]; seen_ids=set()
    for d in walk_dicts(obj):
        s=d.get(must_have)
        if isinstance(s,str) and s and s not in seen_ids:
            # Filter obvious nested option/value slugs when extracting categories/tags.
            if must_have=='slug' and ('parentSlug' in d or 'parent_slug' in d or 'attributes' in d or 'children' in d or 'emoji' in d or 'categories' in d):
                records.append(d); seen_ids.add(s)
    return records

def normalize_api_categories(payload):
    out={}
    for d in slugged_records(payload):
        slug=d.get('slug')
        attrs=d.get('attributes') or []
        if isinstance(attrs,dict): attrs=attrs.get('items') or attrs.get('data') or []
        attrset=set()
        if isinstance(attrs,list):
            for a in attrs:
                if isinstance(a,dict) and isinstance(a.get('slug'),str): attrset.add(a['slug'])
        parent=d.get('parentSlug') or d.get('parent_slug')
        # Name-based and nested representations are supported best-effort; hierarchy validation uses the known brief roots.
        out[slug]={'attrs':attrset,'parent':parent,'raw':d}
    return out

def normalize_api_tags(payload):
    return {d['slug'] for d in slugged_records(payload) if isinstance(d.get('slug'),str)}

def label_ok(label):
    return isinstance(label,dict) and isinstance(label.get('fr'),str) and label['fr'].strip() and isinstance(label.get('en'),str) and label['en'].strip()

def validate_attribute(a, where, checks):
    if not isinstance(a,dict): checks.err(f'{where}: attribut non objet'); return
    slug=a.get('slug')
    if not isinstance(slug,str) or not ATTR_SLUG_RE.fullmatch(slug): checks.err(f'{where}: slug d’attribut invalide: {slug!r}')
    if not label_ok(a.get('name')): checks.err(f'{where}/{slug}: name doit contenir fr et en')
    typ=a.get('type')
    if typ not in ALLOWED_TYPES: checks.err(f'{where}/{slug}: type invalide {typ!r}')
    if not isinstance(a.get('required'),bool) or not isinstance(a.get('filterable'),bool): checks.err(f'{where}/{slug}: required/filterable doivent être booléens')
    if typ=='select':
        opts=a.get('options')
        if not isinstance(opts,list) or len(opts)<2: checks.err(f'{where}/{slug}: select doit avoir au moins 2 options')
        else:
            vals=[]
            for i,o in enumerate(opts):
                if not isinstance(o,dict): checks.err(f'{where}/{slug}: option {i} non objet'); continue
                v=o.get('value')
                if not isinstance(v,str) or not VALUE_RE.fullmatch(v): checks.err(f'{where}/{slug}: value invalide {v!r}; snake_case anglais attendu')
                vals.append(v)
                if not label_ok(o.get('label')): checks.err(f'{where}/{slug}: label d’option {v!r} doit contenir fr et en')
            if len(vals)!=len(set(vals)): checks.err(f'{where}/{slug}: valeurs d’options dupliquées')
    if typ=='number':
        for k in ('min','max'):
            if k in a and (not isinstance(a[k],(int,float)) or isinstance(a[k],bool)): checks.err(f'{where}/{slug}: {k} doit être numérique')
        if 'min' in a and 'max' in a and a['min']>a['max']: checks.err(f'{where}/{slug}: min > max')
    if 'group' in a and not label_ok(a['group']): checks.err(f'{where}/{slug}: group doit contenir fr et en')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--offline',action='store_true',help='Ne tente pas de lire l’API publique')
    args=ap.parse_args(); c=Checks()
    filenames=['categories.new.json','attributes.existing.json','aliases.existing.json','tags.new.json']
    data={}
    for fn in filenames:
        try: data[fn]=read_json(fn)
        except RuntimeError as e: c.err(str(e))
    if c.errors:
        print('\n'.join('ERROR '+e for e in c.errors)); return 1
    newcats=data['categories.new.json']
    enrich=data['attributes.existing.json']
    aliases=data['aliases.existing.json']
    tags=data['tags.new.json']
    if not isinstance(newcats,list): c.err('categories.new.json doit être un tableau')
    if not isinstance(enrich,dict) or not isinstance(enrich.get('attributes'),list) or not isinstance(enrich.get('optionAdditions'),list): c.err('attributes.existing.json doit contenir attributes et optionAdditions en tableaux')
    if not isinstance(aliases,list): c.err('aliases.existing.json doit être un tableau')
    if not isinstance(tags,list): c.err('tags.new.json doit être un tableau')

    api_cats=None; api_tags=None; api_status={}
    if not args.offline:
        for key,url in [('categories',API_CATEGORIES),('tags',API_TAGS)]:
            try:
                payload=fetch_json(url)
                if key=='categories':
                    parsed=normalize_api_categories(payload)
                    if not parsed: raise ValueError('la réponse ne contient aucun objet catégorie identifiable')
                    api_cats=parsed
                else:
                    parsed=normalize_api_tags(payload)
                    if not parsed: raise ValueError('la réponse ne contient aucun slug de tag identifiable')
                    api_tags=parsed
                api_status[key]='accessible'
            except Exception as e:
                api_status[key]=f'inaccessible ({type(e).__name__}: {e})'
                c.warn(f'API {key}: lecture impossible — {e}')
    else:
        api_status={'categories':'ignorée (--offline)','tags':'ignorée (--offline)'}
        c.warn('Mode offline : les slugs/attributs/tags non présents dans le brief n’ont pas pu être comparés au payload réel de l’API.')

    existing_cat_slugs=set(EXISTING_CATS)
    if api_cats: existing_cat_slugs |= set(api_cats)
    existing_tag_slugs=set(KNOWN_TAGS)
    if api_tags: existing_tag_slugs |= set(api_tags)
    cat_slug_set=set(existing_cat_slugs)
    # Existing category names and parent map are the confirmed 31-slug brief baseline.
    pmap=dict(PARENT); names=dict(NAMES); new_by_slug={}
    for i,n in enumerate(newcats):
        if not isinstance(n,dict): c.err(f'categories.new.json[{i}] doit être un objet'); continue
        slug=n.get('slug')
        if not isinstance(slug,str) or not SLUG_RE.fullmatch(slug): c.err(f'categories.new.json[{i}]: slug invalide {slug!r}')
        elif slug in cat_slug_set: c.err(f'category slug {slug!r} duplique un slug existant ou proposé')
        else: cat_slug_set.add(slug)
        if not label_ok(n.get('name')): c.err(f'{slug}: name doit contenir fr et en')
        if not isinstance(n.get('searchAliases'),str): c.err(f'{slug}: searchAliases doit être une chaîne')
        if not isinstance(n.get('icon'),str) or not n['icon'].strip(): c.err(f'{slug}: icon absent')
        if not isinstance(n.get('attributes'),list): c.err(f'{slug}: attributes doit être un tableau')
        if not isinstance(n.get('sources'),list) or not n['sources']: c.err(f'{slug}: sources doit être un tableau non vide')
        for s in n.get('sources',[]) if isinstance(n.get('sources'),list) else []:
            if not all(isinstance(s.get(k),str) and s[k].strip() for k in ('marketplace','url','path')): c.err(f'{slug}: source doit avoir marketplace, url, path')
        if 'listingForm' in n:
            f=n['listingForm']
            if not isinstance(f,dict) or f.get('type') not in ALLOWED_FORM_TYPES: c.err(f'{slug}: listingForm.type invalide')
            else:
                for k in ('price','photos'):
                    if f.get(k) not in ALLOWED_FORM_MODES: c.err(f'{slug}: listingForm.{k} invalide')
        if isinstance(slug,str):
            new_by_slug[slug]=n; pmap[slug]=n.get('parentSlug'); names[slug]=n.get('name',{})

    # Parents must exist, and cycles/depth > 3 are rejected.
    all_known=set(existing_cat_slugs)|set(new_by_slug)
    for slug,n in new_by_slug.items():
        parent=n.get('parentSlug')
        if parent is not None and parent not in all_known: c.err(f'{slug}: parentSlug introuvable: {parent!r}')
        seen={slug}; cur=parent; depth=1
        while cur is not None and cur in pmap and cur not in seen:
            seen.add(cur); depth+=1; cur=pmap.get(cur)
        if cur in seen: c.err(f'{slug}: cycle détecté dans parentSlug')
        if depth>3: c.err(f'{slug}: profondeur {depth}, maximum 3')
    if not c.errors: c.ok(f'{len(newcats)} slugs de nouvelles catégories uniques; 31 slugs existants connus')

    # Attribute uniqueness within one category and no redefinition on descendants.
    new_attr_map=defaultdict(set)
    category_added_attrs=defaultdict(set)
    for slug,n in new_by_slug.items():
        seen=set()
        for a in n.get('attributes',[]) if isinstance(n.get('attributes'),list) else []:
            validate_attribute(a,slug,c)
            if isinstance(a,dict) and isinstance(a.get('slug'),str):
                if a['slug'] in seen: c.err(f'{slug}: attribut {a["slug"]!r} dupliqué dans la catégorie')
                seen.add(a['slug']); new_attr_map[slug].add(a['slug']); category_added_attrs[slug].add(a['slug'])
    enrich_attr_map=defaultdict(set)
    for i,entry in enumerate(enrich.get('attributes',[]) if isinstance(enrich,dict) else []):
        if not isinstance(entry,dict): c.err(f'attributes.existing.json.attributes[{i}] doit être un objet'); continue
        cat=entry.get('categorySlug')
        if cat not in EXISTING_CATS: c.err(f'enrichissement: categorySlug inconnu {cat!r}')
        if not isinstance(entry.get('attributes'),list): c.err(f'{cat}: attributes doit être un tableau'); continue
        seen=set()
        for a in entry['attributes']:
            validate_attribute(a,f'attributes.existing.json/{cat}',c)
            if isinstance(a,dict) and isinstance(a.get('slug'),str):
                if a['slug'] in seen: c.err(f'{cat}: attribut {a["slug"]!r} dupliqué dans les ajouts')
                seen.add(a['slug']); enrich_attr_map[cat].add(a['slug']); category_added_attrs[cat].add(a['slug'])
        for s in entry.get('sources',[]):
            if not all(isinstance(s.get(k),str) and s[k].strip() for k in ('marketplace','url','path')): c.err(f'{cat}: source doit avoir marketplace, url, path')
    # Known live/current attributes from API, when found, must not be added again.
    current_attrs=defaultdict(set)
    if api_cats:
        for slug,item in api_cats.items(): current_attrs[slug] |= item['attrs']
    for cat,attrs in enrich_attr_map.items():
        duplicates=attrs & current_attrs.get(cat,set())
        for s in sorted(duplicates): c.err(f'{cat}: attribut ajouté {s!r} existe déjà dans l’API')
    # Inheritance check: no child repeats ancestor attributes, including proposed enrichments.
    all_attr_map=defaultdict(set)
    for root in ROOTS: all_attr_map[root] |= KNOWN_ATTRS_BY_CATEGORY[root]
    for cat, attrs in current_attrs.items(): all_attr_map[cat] |= attrs
    for cat, attrs in enrich_attr_map.items(): all_attr_map[cat] |= attrs
    for cat, attrs in new_attr_map.items(): all_attr_map[cat] |= attrs
    for slug,n in new_by_slug.items():
        parent=n.get('parentSlug'); ancestors=[]; cur=parent
        while cur is not None and cur in pmap and cur not in ancestors:
            ancestors.append(cur); cur=pmap.get(cur)
        inherited=set()
        for ancestor in ancestors: inherited |= all_attr_map.get(ancestor,set())
        overlap=new_attr_map.get(slug,set()) & inherited
        for s in sorted(overlap): c.err(f'{slug}: attribut {s!r} répété alors qu’il est porté par un ancêtre')
    if not c.errors: c.ok('Attributs: types, libellés bilingues, options et unicité locale vérifiés')

    # Option additions are unique per target attr and checked against live select options when possible.
    option_adds=enrich.get('optionAdditions',[]) if isinstance(enrich,dict) else []
    option_seen=set()
    for i,o in enumerate(option_adds):
        if not isinstance(o,dict): c.err(f'optionAdditions[{i}] doit être un objet'); continue
        cat=o.get('categorySlug'); attr_slug=o.get('attributeSlug'); opts=o.get('addOptions')
        if cat not in EXISTING_CATS: c.err(f'optionAdditions[{i}]: categorySlug inconnu {cat!r}')
        if not isinstance(attr_slug,str) or not isinstance(opts,list) or len(opts)<1: c.err(f'optionAdditions[{i}]: attributeSlug/addOptions invalides'); continue
        for op in opts:
            if not isinstance(op,dict) or not isinstance(op.get('value'),str) or not VALUE_RE.fullmatch(op['value']) or not label_ok(op.get('label')):
                c.err(f'optionAdditions[{i}]: option invalide ou libellé non bilingue'); continue
            key=(cat,attr_slug,op['value'])
            if key in option_seen: c.err(f'option dupliquée {key}')
            option_seen.add(key)
        if api_cats and cat in api_cats:
            raw=api_cats[cat]['raw']; attrs=raw.get('attributes') or []
            if isinstance(attrs,dict): attrs=attrs.get('items') or attrs.get('data') or []
            live_options=set()
            if isinstance(attrs,list):
                for a in attrs:
                    if isinstance(a,dict) and a.get('slug')==attr_slug:
                        for op in a.get('options',[]) if isinstance(a.get('options'),list) else []:
                            if isinstance(op,dict): live_options.add(op.get('value'))
            if not live_options: c.warn(f'{cat}.{attr_slug}: impossible de confirmer les options existantes depuis la forme de réponse API')
            for op in opts:
                if isinstance(op,dict) and op.get('value') in live_options: c.err(f'{cat}.{attr_slug}: option {op.get("value")!r} déjà présente dans l’API')

    # Aliases should target existing categories and not repeat the same target.
    alias_targets=set()
    for i,a in enumerate(aliases if isinstance(aliases,list) else []):
        if not isinstance(a,dict) or a.get('categorySlug') not in EXISTING_CATS or not isinstance(a.get('addAliases'),str) or not a['addAliases'].strip(): c.err(f'aliases.existing.json[{i}] invalide')
        elif a['categorySlug'] in alias_targets: c.err(f'aliases.existing.json: categorySlug dupliqué {a["categorySlug"]!r}')
        alias_targets.add(a.get('categorySlug'))

    # Tags
    tag_slugs=set(existing_tag_slugs)
    all_category_slugs=all_known
    for i,t in enumerate(tags if isinstance(tags,list) else []):
        if not isinstance(t,dict): c.err(f'tags.new.json[{i}] doit être un objet'); continue
        slug=t.get('slug')
        if not isinstance(slug,str) or not SLUG_RE.fullmatch(slug): c.err(f'tags.new.json[{i}]: slug invalide {slug!r}')
        elif slug in tag_slugs: c.err(f'tag slug {slug!r} duplique un tag existant ou proposé')
        else: tag_slugs.add(slug)
        if not label_ok(t.get('name')): c.err(f'{slug}: name doit contenir fr et en')
        if not isinstance(t.get('emoji'),str) or not t['emoji'].strip(): c.err(f'{slug}: emoji absent')
        if not isinstance(t.get('categories'),list) or not t['categories']: c.err(f'{slug}: categories doit être un tableau non vide')
        for target in t.get('categories',[]) if isinstance(t.get('categories'),list) else []:
            if target not in all_category_slugs: c.err(f'{slug}: catégorie associée introuvable {target!r}')
        for s in t.get('sources',[]):
            if not all(isinstance(s.get(k),str) and s[k].strip() for k in ('marketplace','url','path')): c.err(f'{slug}: source doit avoir marketplace, url, path')
    if not c.errors: c.ok(f'{len(tags)} tags nouveaux uniques; toutes leurs catégories cibles existent')

    # Build final tree and report.
    all_names=dict(NAMES)
    for slug,n in new_by_slug.items(): all_names[slug]=n.get('name',{'fr':slug,'en':slug})
    def root_for(slug):
        cur=slug; visited=set()
        while cur in pmap and pmap[cur] is not None and cur not in visited:
            visited.add(cur); cur=pmap[cur]
        return cur if cur in ROOTS or cur in new_by_slug and pmap.get(cur) is None else cur
    # Existing slug mapping to proposed root is built via known parent; include all new nodes.
    root_cat_counts=Counter(); root_attr_counts=Counter(); root_tag_counts=Counter()
    for slug in new_by_slug: root_cat_counts[root_for(slug)]+=1
    for slug,attrs in new_attr_map.items(): root_attr_counts[root_for(slug)]+=len(attrs)
    for slug,attrs in enrich_attr_map.items(): root_attr_counts[root_for(slug)]+=len(attrs)
    for t in tags:
        for target in set(t.get('categories',[])):
            root_tag_counts[root_for(target)]+=1
    # Existing roots have correct parent references. New roots are roots in pmap; root_for() works for those.
    tree_children=defaultdict(list)
    for slug,parent in pmap.items():
        if parent is not None: tree_children[parent].append(slug)
    for a in tree_children.values(): a.sort(key=lambda x:(x not in new_by_slug, (all_names.get(x) or {}).get('fr',x).lower()))
    root_order=list(ROOTS)+[s for s,n in new_by_slug.items() if n.get('parentSlug') is None]
    # Add report
    report=[]
    report.append('# Rapport de complétion de la taxonomie BuyNSellem')
    report.append('')
    report.append('## Résumé')
    report.append('')
    report.append(f'- Catégories existantes connues : **{len(EXISTING_CATS)}** (les 31 slugs communiqués dans le brief).')
    report.append(f'- Catégories ajoutées proposées : **{len(newcats)}** ; arborescence totale visée : **{len(EXISTING_CATS)+len(newcats)}** catégories.')
    total_new_attrs=sum(len(n.get('attributes',[])) for n in newcats)+sum(len(x.get('attributes',[])) for x in enrich.get('attributes',[]))
    report.append(f'- Attributs proposés à ajouter : **{total_new_attrs}** (catégories nouvelles + enrichissements des catégories existantes).')
    report.append(f'- Tags ajoutés proposés : **{len(tags)}**. Le brief cite 14 slugs de tags connus, tout en indiquant 16 tags : le payload API doit départager ce décompte.')
    report.append(f'- Aliases proposés pour catégories existantes : **{len(aliases)}** catégories enrichies.')
    report.append('')
    report.append('### Ajouts par racine finale')
    report.append('')
    report.append('| Racine | Catégories ajoutées | Attributs ajoutés | Tags associés (un tag peut couvrir plusieurs racines) |')
    report.append('|---|---:|---:|---:|')
    for r in root_order:
        if r not in ROOTS and r not in new_by_slug: continue
        nm=all_names.get(r,{'fr':r})['fr']
        report.append(f'| {nm} (`{r}`) | {root_cat_counts[r]} | {root_attr_counts[r]} | {root_tag_counts[r]} |')
    report.append(f'| **Total des racines** | **{sum(root_cat_counts.values())}** | **{sum(root_attr_counts.values())}** | **{sum(root_tag_counts.values())}** |')
    report.append('')
    report.append('## Arborescence complète (existant + nouveau)')
    report.append('')
    def emit(slug, depth=0):
        nm=all_names.get(slug,{'fr':slug})['fr']; mark=' **[NOUVEAU]**' if slug in new_by_slug else ''
        report.append('  '*depth+f'- {nm} (`{slug}`){mark}')
        for ch in tree_children.get(slug,[]): emit(ch,depth+1)
    # Root order first preserves the user's current roots before newly added roots.
    for r in ROOTS: emit(r,0)
    for r in root_order:
        if r not in ROOTS and pmap.get(r) is None: emit(r,0)
    report.append('')
    report.append('## Catégories écartées')
    report.append('')
    for item in [
      ('Armes, munitions, explosifs et accessoires destinés à leur usage','Produits dangereux ou réglementés ; interdits par le brief.'),
      ('Drogues, substances illicites et accessoires de consommation','Illégal ou à haut risque ; interdits par le brief.'),
      ('Médicaments sur ordonnance et produits pharmaceutiques contrôlés','Risque sanitaire et réglementation ; seule une parapharmacie hors ordonnance est prévue.'),
      ('Contenus et produits pour adultes','Non retenus dans une marketplace généraliste grand public.'),
      ('Contrefaçons, répliques non autorisées et produits portant atteinte aux marques','Risque légal et de fraude ; la revente d’un produit authentique reste autorisée.'),
      ('Espèces protégées, faune sauvage et produits dérivés','Risque de commerce illicite et de conservation ; animaux domestiques/élevage autorisés seulement.'),
      ('Documents officiels, titres d’identité, permis, diplômes ou certificats à vendre','Risque de fraude et de falsification ; les titres fonciers peuvent être renseignés comme caractéristique d’un terrain, jamais vendus comme document.'),
      ('Produits et services illégaux au Cameroun, biens volés et accès frauduleux','Interdiction générale ; à traiter aussi dans la modération et les conditions d’utilisation.'),
      ('Médicaments vétérinaires sur ordonnance et espèces non domestiques protégées','Les fournitures d’élevage peuvent être listées, mais pas les produits vétérinaires réglementés ni espèces interdites.'),
      ('Catégories peu prioritaires : caravaning lourd, nautisme de loisir, objets de collection spécialisés et actifs numériques','Non ajoutés dans cette première version faute de signal de demande locale suffisant par rapport aux besoins prioritaires ; ajoutables plus tard si les volumes le justifient.')]:
        report.append(f'- **{item[0]}** — {item[1]}')
    report.append('')
    report.append('## Doutes et choix à arbitrer par un humain')
    report.append('')
    for d in [
      '**API de référence non lue dans l’environnement de génération.** Le navigateur expose les endpoints JSON comme une réponse opaque et la requête HTTP depuis l’environnement échoue. Les propositions sont fondées sur les 31 slugs du brief et devront être comparées au payload réel avant import pour confirmer tous les attributs existants et tous les tags existants.',
      '**Écart sur le nombre de tags.** Le brief annonce 16 tags mais ne nomme que 14 slugs. Le script compare aux slugs explicitement cités, puis récupère la liste complète si l’API est accessible.',
      '**Nouveaux attributs des catégories existantes.** Ils sont conçus comme des ajouts, mais leur absence dans le schéma en production ne peut pas être garantie sans lecture de l’API. Le validateur remonte les collisions lorsqu’il peut lire celle-ci.',
      '**Biens immobiliers et documents.** `title-document-type` décrit le titre déclaré d’un terrain ; aucun document officiel ne devient une annonce vendable. Prévoir une notice et des contrôles pour les annonces foncières.',
      '**Animaux et santé.** Ne permettre que les espèces domestiques/élevage légalement autorisées, avec modération dédiée. La catégorie santé n’inclut pas les médicaments sur ordonnance.',
      '**Transfert d’argent et paiements.** La catégorie de service est explicitement limitée aux prestataires agréés ; les tags Mobile Money signalent une modalité déclarée par le vendeur, sans intégrer de paiement ni garantir la transaction.',
      '**Tags de confiance.** `licensed-professional`, `farm-fresh`, `export-ready` et `after-sales-support` sont des déclarations de vendeurs ; un futur mécanisme de vérification est préférable avant d’afficher un badge de confiance.',
      '**Attributs texte filtrables.** Les valeurs marque, modèle ou compatibilité sont souvent libres sur les petites annonces ; normaliser les marques (sans supprimer les valeurs d’origine) et gérer les synonymes de recherche dans l’index de recherche.',
      '**Services sous `jobs-services > services`.** La limite de trois niveaux interdit une quatrième couche de services spécialisés ; les services sont donc des feuilles de niveau 3 et ne doivent pas recevoir de sous-catégories supplémentaires.',
      '**Sources de comparaison.** Les chemins sont documentés dans `sources`; CoinAfrique Cameroon et Boncoin Cameroon sont prioritaires pour l’adaptation locale, tandis que Jiji Nigeria, Jumia Nigeria, Leboncoin, eBay et Amazon servent surtout à approfondir les sous-catégories et filtres.'
    ]: report.append('- '+d)
    report.append('')
    report.append('## Sources consultées')
    report.append('')
    source_list=[
      ('CoinAfrique Cameroon — catégorie principale et familles locales','https://cm.coinafrique.com/','Véhicules, électronique, mode & beauté, immobilier, maison, emploi, services, enfant, loisirs, alimentation, animaux et matériel professionnel.'),
      ('CoinAfrique Cameroon — Matériel Pro','https://cm.coinafrique.com/categorie/materiel-pro','Matériel agricole, BTP/outillage, fournitures de bureau, équipement de commerce, matériel médical et énergie/solaire.'),
      ('CoinAfrique Cameroon — Matériel Agricole','https://cm.coinafrique.com/categorie/materiel-agricole','Matériel agricole et exemples d’équipements de ferme.'),
      ('CoinAfrique Cameroon — Matériaux Construction, BTP, Outillages','https://cm.coinafrique.com/categorie/materiaux-construction-btp-outillages','Matériaux de construction et outillage.'),
      ('Boncoin Cameroon','https://boncoin.cm/','Alimentation/agriculture, construction/réparation, santé/beauté, enfants, équipement professionnel, animaux, restauration, services et véhicules.'),
      ('Jiji Nigeria','https://jiji.ng/','Électronique, maison, beauté, services, réparation/construction, agriculture, animaux, emploi et équipement professionnel.'),
      ('Jumia Nigeria — toutes les catégories','https://www.jumia.com.ng/mlp-all-categories/','Téléphones, informatique, électronique, jardin, instruments, maison/bureau, bébé, santé/beauté, épicerie et industriel.'),
      ('Leboncoin — évolution de l’arborescence','https://assistance.leboncoin.info/hc/fr/articles/14181001891090-Modification-de-l-arbre-des-catégories','Exemples de catégories généralistes : immobilier, véhicules, mode, maison/jardin, matériel professionnel, électronique, loisirs et services.'),
      ('eBay','https://www.ebay.com/','Profondeur d’e-commerce pour pièces auto, électronique, maison/jardin, sport, équipement professionnel, mode et fournitures pour animaux.'),
      ('Amazon Seller Central — catégories','https://sellercentral.amazon.com/gp/help/external/GCZM532MZ9A4GU46?language=en_US','Profondeur d’e-commerce pour bébé, informatique, meubles, maison, animaux, sport, outils, bureau, épicerie et beauté/santé.'),
      ('Glotelho Cameroon','https://glotelho.cm/','Vente en ligne locale : groupes électrogènes, solaire, peinture, jardinage, réseau, informatique, couture africaine, beauté, appareils photo et maternité.'),
      ('Tonaton Ghana','https://tonaton.com/','Arborescence régionale structurée en véhicules, immobilier, électronique, maison, beauté, réparation/construction, matériel professionnel, agriculture/élevage, animaux et emploi.'),
      ('Afrimalin Senegal — taxonomie d’annonces','https://afrimalin.sn/','Téléphones/tablettes, informatique, TV/multimédia, véhicules et pièces, mode/beauté/santé, produits agricoles, immobilier, maison/décoration, matériel professionnel, culture/loisirs/sport, emplois et services.'),
      ('Expat-Dakar — immobilier commercial','https://www.expat-dakar.com/proprietes-commerciales-a-vendre?sort=latest','Référence de petites annonces immobilières : appartements, chambres, maisons, terrains et propriétés commerciales; complète les choix de location et de vente.'),
      ('KiJOKI Cameroon — toutes les catégories','https://kijoki.com/all-categories','Source locale : automobile, pièces auto, pneus/accessoires, terrains/logements, santé/beauté, sport, location, électronique, emploi, services, enfants et alimentation.'),
      ('Kaymu / Jumia Market — archive historique, non utilisée comme source active','https://techpoint.africa/general/jumia-market-shut-down-operation/','Kaymu a été intégré à Jumia Market/Jumia dans la consolidation de 2016–2017; référence historique seulement, pas une taxonomie active à copier.'),
      ('Facebook / Marketplace — lancement et filtres de recherche','https://about.fb.com/news/2016/10/introducing-marketplace-buy-and-sell-with-your-local-community/','Référence générale pour les annonces locales et le filtrage par catégorie, prix et localisation.')]
    for title,url,desc in source_list: report.append(f'- [{title}]({url}) — {desc}')
    report.append('')
    report.append('## Résultat de validation')
    report.append('')
    report.append('Le résultat local détaillé est livré dans `validation.txt`. Lorsque l’API est inaccessible, ce rapport ne doit pas être interprété comme une confirmation que les attributs/tags cachés n’existent pas déjà.')
    (BASE/'report.md').write_text('\n'.join(report)+'\n',encoding='utf-8')

    # Print validation output.
    print('BuyNSellem taxonomy validator')
    print('Files:', ', '.join(filenames))
    print('API status:', json.dumps(api_status,ensure_ascii=False))
    print(f'Known current categories: {len(EXISTING_CATS)}; proposed categories: {len(newcats)}; final total: {len(EXISTING_CATS)+len(newcats)}')
    print(f'Proposed new tags: {len(tags)}; alias entries: {len(aliases)}')
    for m in c.passed: print('PASS:',m)
    for m in c.warnings: print('WARN:',m)
    for m in c.errors: print('FAIL:',m)
    print(f'STATUS: {"FAIL" if c.errors else ("PASS WITH WARNINGS" if c.warnings else "PASS")} ({len(c.errors)} errors, {len(c.warnings)} warnings)')
    out=[]
    out.append('BuyNSellem taxonomy validator')
    out.append('Files: '+', '.join(filenames))
    out.append('API status: '+json.dumps(api_status,ensure_ascii=False))
    out.append(f'Known current categories: {len(EXISTING_CATS)}; proposed categories: {len(newcats)}; final total: {len(EXISTING_CATS)+len(newcats)}')
    out.append(f'Proposed new tags: {len(tags)}; alias entries: {len(aliases)}')
    out.extend('PASS: '+m for m in c.passed); out.extend('WARN: '+m for m in c.warnings); out.extend('FAIL: '+m for m in c.errors)
    out.append(f'STATUS: {"FAIL" if c.errors else ("PASS WITH WARNINGS" if c.warnings else "PASS")} ({len(c.errors)} errors, {len(c.warnings)} warnings)')
    (BASE/'validation.txt').write_text('\n'.join(out)+'\n',encoding='utf-8')
    return 1 if c.errors else 0

if __name__=='__main__': sys.exit(main())
