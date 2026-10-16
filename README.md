# geo2stl

Console application Python per convertire GeoTIFF altimetrici in solidi STL binari, con coordinate in **millimetri**. Superficie, fondo e pareti seguono il contorno dei dati validi: non viene aggiunta una base rettangolare sotto pixel esclusi o fori.

## Installazione

Richiede Python 3.10 o successivo. Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Su Windows usare `py -m venv .venv` e `.venv\Scripts\Activate.ps1` in PowerShell.

È già disponibile un ambiente `.venv` nel workspace. Anche senza installare il comando si può usare `.venv/bin/python -m geo2stl`.

## Metadati

```bash
geo2stl --info Veneto30x30_ritagliato.tif
geo2stl --info Veneto30x30_ritagliato.tif --stats --json
```

Mostra dimensioni, bande, tipo numerico, CRS, estensione, risoluzione, trasformazione affine, NoData, maschere, unità, scale/offset e tag. `--stats` legge tutta la banda per contare pixel validi/invalidi/zero e calcolare quote minime/massime; senza questa opzione vengono letti solo i metadati. `--band` sceglie la banda, default 1. `--nodata` influisce anche sulle statistiche.

## Ispezione di un pixel

```bash
geo2stl --sample 0 0 Veneto30x30_ritagliato.tif
geo2stl --sample 120 350 Veneto30x30_ritagliato.tif --json
geo2stl --sample 120 350 Veneto30x30_ritagliato.tif --nodata 0 --elevation-offset 200
```

Gli indici sono **X = colonna, Y = riga**, a partire da zero nell'angolo in alto a sinistra. Si legge soltanto una finestra di un pixel dalla banda scelta con `--band`; indici fuori dal raster generano un errore.

Il comando mostra valore grezzo, quota in metri dopo scale/offset e conversione di unità, NoData dichiarato, valore della maschera (0 = invalido, positivo = valido), validità secondo il file e secondo `--nodata`, confronto con la quota di taglio e coordinate del centro nel CRS del raster. `eligible_for_mesh` indica che il punto supera maschera e taglio; la presenza effettiva nella mesh dipende anche da campionamento, triangoli adiacenti e regolarizzazione del contorno. `--stride` non cambia gli indici ispezionati.

Nel GeoTIFF ritagliato aggiornato, il pixel `(0, 0)` contiene **0.0**, con maschera **255** e NoData non dichiarato. Per escludere gli zeri esterni al ritaglio usare `--nodata 0`: il file non li identifica automaticamente come invalidi.

## Conversione

```bash
geo2stl --convert Veneto30x30_ritagliato.tif \
  --scale 1:100000 \
  --elevation_offset 200 \
  --vertical_exageration 1.5 \
  --base_height 2 \
  --nodata 0 \
  --stride 10 \
  --output veneto.stl
```

Sono supportati sia i nomi con underscore proposti, incluso `vertical_exageration`, sia i nomi `--elevation-offset`, `--vertical-exaggeration` e `--base-height`.

| Opzione | Significato | Default |
| --- | --- | --- |
| `--scale 1:N` | Scala orizzontale; accetta anche `N` | `1:100000` |
| `--elevation-offset` | Quota di taglio in metri; conserva solo quote **>** del valore | `0` |
| `--vertical-exaggeration` | Moltiplicatore del rilievo verticale | `1` |
| `--base-height` | Spessore minimo del solido, in mm | `2` |
| `--stride N` | Un punto ogni N pixel, in entrambe le direzioni | `1` |
| `--nodata V` | Esclude il valore grezzo V, oltre alla maschera nativa | nessuno |
| `--elevation-unit m/ft` | Forza unità delle quote | metadati, altrimenti metri |
| `--output`, `-o` | Percorso di destinazione | nome dell'input con `.stl` |
| `--max-points` | Limite della griglia campionata per contenere memoria e dimensioni | `2000000` |
| `--strict-boundary` | Interrompe se aree valide si toccano soltanto in un punto | disattivato |
| `--force` | Consente di sovrascrivere un output esistente | disattivato |

Quote e spessore vengono calcolati così, con `q` in metri e `N` denominatore della scala:

```text
z_mm = base_height + (q - elevation_offset) × 1000 / N × vertical_exaggeration
```

Il fondo è a `z = 0`; ogni punto della superficie ha quindi spessore almeno `base_height`. Offset significa **taglio e sottrazione della quota**, non traslazione verso l'alto. La stessa scala si applica a XY e al rilievo prima dell'esagerazione verticale. Gli STL non codificano unità: importarli nello slicer come millimetri.

## NoData e contorni

Si rispettano le maschere della banda (anche maschere GDAL/alpha), il NoData dichiarato e i valori non finiti. Lo zero non è automaticamente NoData: può essere una quota reale. Usare `--nodata 0` se è il valore scelto per l'area priva di dati. Questa opzione confronta il valore grezzo prima di scale/offset della banda.

I vertici sono i centri dei pixel campionati, nelle coordinate del GeoTIFF. Ogni cella viene triangolata usando solo vertici validi: tre angoli validi possono produrre un triangolo, quattro ne producono due. Non si interpola sul limite della quota di taglio e non si collegano punti attraversando regioni escluse. Si generano facce inferiori e pareti soltanto per questi triangoli, incluse pareti interne attorno ai fori. I bordi seguono quindi la griglia e sono leggermente interni rispetto all'estensione dei pixel.

Il taglio e la maschera possono creare contatti puntuali: estrudendoli si otterrebbero bordi condivisi da più di due facce. Per default vengono rimossi iterativamente i punti di contatto, segnalando il numero di punti esclusi. Questo restringe localmente il contorno. `--strict-boundary` evita questa modifica e restituisce un errore. Aree isolate sufficientemente grandi restano solidi separati nello stesso STL; punti o linee senza triangoli non vengono esportati. Se non rimane alcun triangolo, la conversione fallisce.

`--stride` campiona i pixel originali, senza media o interpolazione: può perdere dettagli, piccole isole o piccoli fori e modifica il contorno rispetto alla risoluzione originale. Usare `1` per tutti i punti, aumentando esplicitamente `--max-points` solo se si dispone della memoria necessaria. La memoria per la mesh cresce con i punti campionati; lo STL viene scritto a blocchi in un file temporaneo e pubblicato soltanto a conversione conclusa.

## Coordinate e unità

I CRS proiettati vengono convertiti da unità del CRS a metri. Per coordinate geografiche, come negli esempi EPSG:4326, si usa una proiezione azimutale equidistante locale (AEQD) centrata sul raster. Le distanze dal centro sono preservate; su estensioni grandi le altre distanze subiscono distorsioni. Un CRS mancante genera un errore: il tool non ne indovina uno. Sono rispettate rotazioni e inclinazioni della trasformazione affine.

Le quote vengono corrette con scale/offset della banda, convertite da piedi se dichiarato e altrimenti assunte in metri con un messaggio. Per altri sistemi verticali serve prima una conversione delle quote; non viene applicata una trasformazione del datum verticale.

## File di esempio

Il GeoTIFF originale ha 9532 × 7286 pixel; il ritagliato aggiornato ha **8924 × 6792 pixel**. Entrambi usano EPSG:4326, una banda float32, nessun NoData dichiarato e nessuna unità verticale dichiarata. Nel ritagliato l'area esterna è rappresentata da zeri: usare `--nodata 0` per escluderla anche con offset negativo.

Con scala `1:100000`, offset 200, esagerazione 1.5 e stride 30, il modello ritagliato aggiornato misura circa **1580.7 × 1548.8 × 44.8 mm**. Per un modello di circa 16 cm:

```bash
geo2stl --convert Veneto30x30_ritagliato.tif \
  --scale 1:1000000 --elevation-offset 200 \
  --vertical-exaggeration 1.5 --base-height 2 \
  --nodata 0 --stride 30 -o examples/veneto_ritagliato.stl
```

È incluso `examples/veneto_ritagliato.stl`, rigenerato dal GeoTIFF ritagliato aggiornato con questi parametri: circa **158.1 × 154.9 × 6.3 mm**, 42.432 triangoli. A stride 30 vengono esclusi 11 punti per regolarizzare i contatti puntuali. Il file a stride 1 potrebbe raggiungere diversi gigabyte: il limite predefinito richiede di scegliere un campionamento più leggero.

## Verifica

```bash
python -m pytest -q
```

I test verificano chiusura e orientamento dei bordi, normali, volume positivo, dimensioni e scala, fori senza fondo, NoData/maschere/NaN, quote zero, contatti puntuali, coordinate geografiche e trasformazioni ruotate, unità verticali, metadati e protezione da sovrascrittura.
