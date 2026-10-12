<?php

// The indexed entity. Deliberately the smallest thing with a mapped id and
// one text field: this cell proves that `fos:elastica:populate` reads a
// Doctrine source and writes documents, not that Elastica maps a rich schema.

namespace App\Entity;

use Doctrine\ORM\Mapping as ORM;

#[ORM\Entity]
#[ORM\Table(name: 'doc')]
class Doc
{
    #[ORM\Id]
    #[ORM\Column]
    public int $id = 0;

    #[ORM\Column(length: 255)]
    public string $title = '';
}
