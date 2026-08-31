# FB EMM - Contrôle et affectation des PCO

Première fonction de l'application FB EMM :

1. saisir un SPL, par exemple `OFAD33-ZO-113.16` ;
2. générer les 12 formes possibles de PCO ;
3. contrôler chaque PCO dans WimTech avec Selenium ;
4. détecter et afficher les ports libres ;
5. conserver les PCO disponibles dans `data/available_pcos.json` pour les prochaines fonctions.

## Affectation automatique d’un Login

L’onglet **Affectation automatique** reçoit un Login client et son SPL. Il
parcourt les mêmes PCO possibles et applique la même règle 8 FO / 4 FO :

1. recherche du Login avec le mode radio `Login` ;
2. suppression de l’ancienne constitution modifiable ;
3. choix du motif `BSFB`, puis double validation de la suppression ;
4. ouverture de la nouvelle constitution avec `frm:dataTable81` ;
5. test du PCO puis ouverture du câble `(FO4-Active)` ou `(FO8-Active)` ;
6. sélection du premier port `FIBRE-Libre` ou `En cours decon` ;
7. clic sur le lien `Muter vers (+)` associé au port ;
8. validation avec `frm:dataTable94`, puis `frm:bt_va` ;
9. fermeture de la confirmation avec `frm:v_but_ano`.

Dès que l’affectation est confirmée, le parcours s’arrête. Si aucun port n’est
utilisable, l’interface conserve la liste complète avec le motif de chaque PCO
(`Saturé`, `Inexistant`, `Ignoré` ou erreur). Si le navigateur perd la réponse
après le clic sur `+`, l’automatisation s’arrête en état **À confirmer** afin de
ne pas risquer une deuxième mutation du même Login.

L'affectation peut aussi être lancée en lot avec un fichier Excel contenant
les colonnes `Login` et `SPL`. Les lignes sont traitées l'une après l'autre avec
exactement la même recherche des PCO et du premier brin utilisable. Une mutation
à confirmer arrête le lot afin d'éviter toute double mutation.

Une correspondance PORT MSAN → SPL peut être importée dans **Configuration** au
format `.xlsx`, `.xlsm` ou `.csv`, avec les colonnes `Carte` et
`Splitter ou SRO`. Dans **Affectation automatique**, la recherche d'une carte,
par exemple `GHI-FF-AinKadous:0-0-3-0`, remplit automatiquement son SPL avant
l'affectation.

Il est aussi possible de coller uniquement une liste de Logins, un par ligne.
Pour chaque Login, Selenium lit `Nom Usuel` et `Ne` dans
`frm:NumeroEquipementGPON`, supprime le dernier segment de `Ne`, puis ajoute un
préfixe `0-` pour les équipements Huawei dont le nom commence par `MH` ou `GH`.
La clé PORT MSAN obtenue sert à retrouver le SPL dans la correspondance importée.
La lecture attend maintenant que RichFaces ait réellement rempli les cellules et
utilise aussi leur `textContent`, ce qui évite les erreurs transitoires lorsque le
tableau existe déjà mais que son texte n'est pas encore rendu par Selenium.

Exemple validé : `MHOu-Fe-MourabitineERAC1--2C2` avec `Ne=0-18-8-7` produit
`MHOu-Fe-MourabitineERAC1--2C2:0-0-18-8`.

Si WimTech retourne `pas de port disponible au niveau fibre optique` pendant
une validation, ce cas est enregistré comme **Port indisponible**. Il n'est pas
considéré comme une mutation incertaine : le traitement passe au PCO ou au
Login suivant.

## Bulk Mutation CMD&Login

L’onglet **Bulk Mutation CMD&Login** accepte un fichier `.xlsx` ou `.xlsm` et
utilise cinq colonnes :

- `Commande GPON` ;
- `Login` ;
- `ODF` (valeur ZR/SRO complète, par exemple `OFOF-ZO`) ;
- `PCO` (par exemple `2711`, `2711/1` ou `2711/2`) ;
- `brin`.

Exemple : `100154532 · SaLogin · OFOF-ZO · 2711 · 6`. L’application en déduit
`ODF = OFOF`, `ZR/SRO = OFOF-ZO` et concatène le PCO complet
`OFOF-ZO-2711`.

Pour un PCO 4 FO portant le suffixe `/1` ou `/2`, les brins Excel `5`, `6`,
`7`, `8` sont respectivement convertis en ports `1`, `2`, `3`, `4`. Les PCO
8 FO sans suffixe conservent les brins `1` à `8` sans conversion.

Pour chaque ligne, Selenium essaie la Commande avec le radio `NNETO`. Si
`frm:ot_4` indique qu’aucun circuit n’est associé à la commande, il recommence
avec le radio `Login`. Il ouvre ensuite le PCO exact et cible le `brin` fourni
par Excel. Le lien **Muter vers (+)** est utilisé même si la fibre est Active ;
le Login actuellement affiché sur cette fibre est mémorisé avant le clic. Le
SPL est également lu dans `frm:constitutionList` avant la suppression de
l'ancienne constitution et ajouté au fichier Excel de résultats.

Avant **Ajouter**, l’ancienne constitution modifiable est supprimée selon la
séquence `case cochée` → `frm:dataTable82` → `frm:dataTable94` → motif `BSFB` →
`frm:v_but_va` → `frm:v_but_ano`. La ligne amont dont la case est désactivée
n’est jamais sélectionnée. Cette suppression est utilisée uniquement par les
fonctions de mutation ; le simple **Contrôle des PCO** ne supprime rien.

La séquence finale reste `frm:dataTable94` → `frm:bt_va` → `frm:v_but_ano`.
Un PCO absent est retourné sous la forme `PCO introuvable : <PCO>`. Un fichier
Excel des résultats peut être téléchargé à la fin avec la recherche utilisée,
le Login précédent, l’état et le message de chaque ligne.

### Règle 8 FO / 4 FO

Pour chaque base, l'application contrôle toujours le PCO sans suffixe en
premier. Exemple : `OFAD33-ZO-31611`.

- Si cette base existe, elle représente le PCO 8 FO : `31611/1` et `31611/2`
  sont marqués **Ignoré** et ne sont pas recherchés dans WimTech.
- Si cette base est introuvable, elle est considérée comme découpée en deux
  PCO 4 FO : l'application contrôle alors `/1` puis `/2`.
- En cas d'erreur technique sur la base, `/1` et `/2` sont quand même contrôlés
  car l'existence du PCO 8 FO n'a pas été confirmée.

## Installation et exécution en mode développement sous Windows

Prérequis :

- Python ajouté au `PATH` ;
- Google Chrome installé ;
- accès au réseau interne et à `http://wimtech`.

Ouvrir **Invite de commandes (CMD)** dans le dossier du projet, puis créer
l'environnement virtuel :

```bat
py -m venv .venv
```

Vérifier sa création :

```bat
dir .venv\Scripts
```

Le dossier doit notamment contenir :

```text
activate.bat
Activate.ps1
pip.exe
python.exe
```

Activer ensuite l'environnement virtuel depuis CMD :

```bat
call .venv\Scripts\activate.bat
```

L'invite de commandes doit maintenant commencer par `(.venv)`, par exemple :

```text
(.venv) C:\Users\Samir\Desktop\projet IAM\local_app>
```

Installer les dépendances du projet :

```bat
python -m pip install -r requirements.txt
```

Lancer enfin l'application en mode développement local :

```bat
python app.py
```

Ouvrir `http://127.0.0.1:5055` dans le navigateur. Pour arrêter le serveur,
utiliser `Ctrl+C`. Lors d'une prochaine session, il suffit de réactiver
l'environnement avec `call .venv\Scripts\activate.bat`, puis de relancer
`python app.py`.

### Démarrage automatique sous Windows

Il est également possible de double-cliquer sur `run.bat`. Le script crée
l'environnement `.venv` si nécessaire, installe Flask et Selenium, démarre
l'application, puis ouvre automatiquement `http://127.0.0.1:5055`.

## Configuration

Dans l'onglet **Configuration**, vérifier :

- l'URL Mutation GPON ;
- le Login de test utilisé pour ouvrir la page d'ajout de constitution ;
- le délai Selenium ;
- le mode Chrome visible ou arrière-plan.

La valeur MVP du Login de test est : `I10260472`.

## Workflow Selenium utilisé

Le contrôle reprend les identifiants déjà présents dans `splt.py` et Cuivre V2 :

- `input[name='frm:radionRechrche'][value='Login']` : mode de recherche Login ;
- `frm:in_2` : Login de test ;
- `frm:bt_1` : rechercher ;
- `frm:bt_2` : valider ;
- `frm:dataTable81` : ajouter une constitution ;
- `fr:inputOdf` : ODF ;
- `fr:inputZro` : zone réseau ;
- `fr:inputEquipAmont` : PCO candidat ;
- `fr:b_et` : lancer l'étude.

Après validation, `ot_1` permet de reconnaître un PCO inexistant. Pour un PCO
existant, Selenium ouvre le premier câble `(FO4-Active)` ou `(FO8-Active)`, puis
collecte les ports dont le libellé contient `FIBRE-Libre` ou `En cours decon`.
Les états `En service` et `En cours` ne sont pas considérés comme disponibles.
Enfin, Selenium clique sur `frm:bt_an` (**Annuler**) et recommence tout le flux
de recherche Login pour le PCO suivant.

Si `ot_1` affiche `Nom du ODF invalide`, l’application applique automatiquement
la règle `OXXX` → `OMSANXXX` et soumet une deuxième fois le même ZR et le même
PCO. Exemple : `OFOF` devient `OMSANFOF`. Cette correction s’applique au
contrôle, à l’affectation automatique et au traitement Bulk.

Si aucun câble actif n'est reconnu, la ligne apparaît **À vérifier** et la page
HTML est enregistrée dans `diagnostics/` pour permettre un ajustement précis.

## Fichiers importants

- `app.py` : API locale, tâches, pause/reprise/arrêt et exports ;
- `pco_logic.py` : génération SPL → PCO ;
- `wimtech_checker.py` : automatisation Selenium ;
- `wimtech_assigner.py` : affectation automatique au premier port utilisable ;
- `wimtech_bulk_mutator.py` : mutations Excel CMD/Login sur PCO et brin exacts ;
- `bulk_excel.py` : lecture des quatre colonnes et export des résultats ;
- `templates/index.html` : interface inspirée de Cuivre V2 ;
- `static/app.js` : progression et résultats en direct ;
- `data/available_pcos.json` : collection destinée aux fonctions suivantes.

## Tests de la génération PCO

Depuis le dossier de l'application :

```powershell
py -m unittest discover -s tests -v
```

## Exécution avec Docker

Docker installe Chromium dans le conteneur et l'utilise automatiquement en
mode headless. Les données et diagnostics restent disponibles sur l'hôte.

```powershell
docker compose up --build -d
```

Ouvrir ensuite `http://127.0.0.1:5055`. Pour consulter les journaux ou arrêter
l'application :

```powershell
docker compose logs -f
docker compose down
```

Le conteneur doit pouvoir résoudre `wimtech` et joindre le réseau interne de
l'entreprise. En cas de VPN ou de DNS interne inaccessible depuis Docker,
configurer le DNS ou le réseau Docker de la machine hôte.

## Installation et mise à jour sur un serveur Linux

Le dépôt GitHub du projet est :
`https://github.com/SamirEzzahir/IAM-Project.git`.

### Première installation

```bash
cd /opt
sudo git clone https://github.com/SamirEzzahir/IAM-Project.git
cd IAM-Project
sudo docker compose up -d --build
```

### Mise à jour

Le fichier `data/msan_spl_mapping.json` peut être modifié par l'application.
Effectuer une copie de sauvegarde avant de récupérer une nouvelle version :

```bash
cd /opt/IAM-Project
git pull origin main
sudo docker compose up -d --build --force-recreate
```

Vérifier ensuite l'état du service et consulter ses journaux :

```bash
sudo docker compose ps
sudo docker compose logs -f fb-emm
```

### Dépôt GitHub privé

Pour un dépôt privé, il est recommandé d'utiliser une clé SSH de
déploiement :

```bash
ssh-keygen -t ed25519 -C "iam-server"
cat ~/.ssh/id_ed25519.pub
```

Ajouter la clé publique affichée aux *Deploy keys* du dépôt GitHub, puis
cloner le projet avec SSH :

```bash
cd /opt
sudo git clone git@github.com:SamirEzzahir/IAM-Project.git
```
