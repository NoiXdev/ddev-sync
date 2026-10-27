"""Seed lists the `fake` sanitize operation draws its values from.

They are embedded into generated SQL as `ELT(...)` literals, so an entry must
never contain a quote or a backslash; the tests pin that. The lists are English
and deliberately generic: they exist to make a local copy look plausible, not
to model any real population.
"""

# ruff: noqa: SIM905
# (whitespace-split strings keep ~600 seed words reviewable as prose, not one per line)

FIRST_NAMES: tuple[str, ...] = tuple(
    """James Mary John Patricia Robert Jennifer Michael Linda William Elizabeth
    David Barbara Richard Susan Joseph Jessica Thomas Sarah Charles Karen
    Christopher Lisa Daniel Nancy Matthew Betty Anthony Margaret Mark Sandra
    Donald Ashley Steven Kimberly Paul Emily Andrew Donna Joshua Michelle
    Kenneth Carol Kevin Amanda Brian Dorothy George Melissa Timothy Deborah
    Ronald Stephanie Edward Rebecca Jason Sharon Jeffrey Laura Ryan Cynthia
    Jacob Kathleen Gary Amy Nicholas Angela Eric Shirley Jonathan Anna
    Stephen Brenda Larry Pamela Justin Emma Scott Nicole Brandon Helen
    Benjamin Samantha Samuel Katherine Gregory Christine Alexander Debra
    Frank Rachel Patrick Carolyn Raymond Janet Jack Catherine Dennis Maria
    Jerry Heather Tyler Diane Aaron Olivia Jose Julie Adam Joyce Nathan
    Victoria Henry Ruth Douglas Virginia Zachary Lauren Peter Kelly Kyle
    Christina Walter Joan Ethan Evelyn Jeremy Judith Harold Andrea Keith
    Hannah Christian Megan Roger Cheryl Noah Jacqueline Gerald Martha Carl
    Gloria Terry Teresa Sean Ann Austin Sara Arthur Madison Lawrence Frances
    Jesse Kathryn Dylan Janice Bryan Jean Joe Abigail Jordan Alice Billy
    Julia Bruce Judy Albert Sophia Willie Grace Gabriel Denise Logan Amber
    Alan Doris Juan Marilyn Wayne Danielle Roy Beverly Ralph Isabella Randy
    Theresa Eugene Diana Vincent Natalie Russell Brittany Elijah Charlotte
    Louis Marie Bobby Kayla Philip Alexis Johnny Lori""".split()
)

LAST_NAMES: tuple[str, ...] = tuple(
    """Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez
    Hernandez Lopez Gonzalez Wilson Anderson Thomas Taylor Moore Jackson Martin
    Lee Perez Thompson White Harris Sanchez Clark Ramirez Lewis Robinson Walker
    Young Allen King Wright Scott Torres Nguyen Hill Flores Green Adams Nelson
    Baker Hall Rivera Campbell Mitchell Carter Roberts Gomez Phillips Evans
    Turner Diaz Parker Cruz Edwards Collins Reyes Stewart Morris Morales
    Murphy Cook Rogers Gutierrez Ortiz Morgan Cooper Peterson Bailey Reed
    Kelly Howard Ramos Kim Cox Ward Richardson Watson Brooks Chavez Wood
    James Bennett Gray Mendoza Ruiz Hughes Price Alvarez Castillo Sanders
    Patel Myers Long Ross Foster Jimenez Powell Jenkins Perry Russell
    Sullivan Bell Coleman Butler Henderson Barnes Gonzales Fisher Vasquez
    Simmons Romero Jordan Patterson Alexander Hamilton Graham Reynolds Griffin
    Wallace Moreno West Cole Hayes Bryant Herrera Gibson Ellis Tran Medina
    Aguilar Stevens Murray Ford Castro Marshall Owens Harrison Fernandez
    McDonald Woods Washington Kennedy Wells Vargas Henry Chen Freeman Webb
    Tucker Guzman Burns Crawford Olson Simpson Porter Hunter Gordon Mendez
    Silva Shaw Snyder Mason Dixon Munoz Hunt Hicks Holmes Palmer Wagner Black
    Robertson Boyd Rose Stone Salazar Fox Warren Mills Meyer Rice Schmidt
    Garza Daniels Ferguson Nichols Stephens Soto Weaver Ryan Gardner Payne
    Grant Dunn Kelley Spencer Hawkins Arnold Pierce Vazquez Hansen Peters
    Santos Hart Bradley Knight Elliott Cunningham Duncan Armstrong Hudson
    Carroll Lane Riley Andrews Alvarado Ray Delgado Berry Perkins Hoffman""".split()
)

CITIES: tuple[str, ...] = tuple(
    """Springfield Riverton Fairview Georgetown Clinton Madison Franklin
    Greenville Bristol Salem Arlington Ashland Burlington Chester Dover
    Hudson Kingston Lexington Manchester Milton Newport Oxford Plymouth
    Richmond Winchester Auburn Camden Dayton Easton Florence Glendale
    Harrison Jackson Lincoln Marion Oakland Portland Quincy Rochester
    Sheffield Trenton Union Vernon Warwick Yorktown Brighton Cambridge
    Durham Lancaster""".split()
)

LOREM_WORDS: tuple[str, ...] = tuple(
    """lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod
    tempor incididunt ut labore et dolore magna aliqua enim ad minim veniam
    quis nostrud exercitation ullamco laboris nisi aliquip ex ea commodo
    consequat duis aute irure in reprehenderit voluptate velit esse cillum
    eu fugiat nulla pariatur excepteur sint occaecat cupidatat non proident
    sunt culpa qui officia deserunt mollit anim id est laborum""".split()
)
